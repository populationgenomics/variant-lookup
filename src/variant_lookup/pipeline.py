"""Per-variant pipeline orchestrator.

Wires the components landed in Phases 1-5 into the end-to-end chain that
``POST /v1/variant`` exposes:

1. parse + clean the raw text (or resolve an rsID via NCBI)
2. normalize via Mutalyzer
3. for ``p.`` inputs, back-translate to a list of coding-variant candidates
4. for each candidate, ask VV for the GRCh38 pseudo-VCF + MANE-select hgvs-c/p
5. look up frequencies via echtvar
6. assemble a :class:`VariantResponse`

Per-stage wall-clock is captured into :class:`ResponseMeta`'s ``durations_ms``
so callers can see where time went without external profiling.
"""

import datetime
import time
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from variant_lookup import __version__, echtvar, ncbi, normalize, versions
from variant_lookup.config import Settings
from variant_lookup.models import (
    NormalizedVariant,
    ResponseMeta,
    VariantError,
    VariantInput,
    VariantResponse,
)
from variant_lookup.mutalyzer_client import MutalyzerClient, MutalyzerError
from variant_lookup.refseq import RefSeqIndex
from variant_lookup.variantvalidator_client import (
    VariantValidatorClient,
    VariantValidatorError,
    VVResult,
)

# Stages reported in ResponseMeta.durations_ms. Listed explicitly (not derived
# from the timing dict) so the response shape is stable even for requests
# whose inputs don't exercise every stage.
_STAGES: tuple[str, ...] = (
    "cleanup",
    "rsid",
    "normalize",
    "back_translate",
    "variantvalidator",
    "echtvar",
    "total",
)


@dataclass
class Pipeline:
    settings: Settings
    refseq_index: RefSeqIndex
    vv_client: VariantValidatorClient
    mutalyzer_client: MutalyzerClient
    # Per-request stage timings (ns). Populated as process_one runs and read
    # by _meta to fill ResponseMeta.durations_ms. Pipeline is constructed
    # per-request in api._lookup_variant so this state is request-scoped.
    _durations_ns: dict[str, int] = field(
        default_factory=lambda: defaultdict(int), init=False, repr=False, compare=False
    )

    def process_one(self, variant: VariantInput, genome_build: str | None) -> VariantResponse:
        with self._timed("total"):
            try:
                normalized_hgvs_strings = self._cleanup_and_normalize(variant, genome_build)
            except _PipelineError as e:
                return self._error_response(e)

            vv_results = self._resolve_via_vv(normalized_hgvs_strings)
            if isinstance(vv_results, _PipelineError):
                return self._error_response(vv_results)

            normalized_variants = [
                NormalizedVariant(
                    pseudo_vcf=r.pseudo_vcf, hgvs_c=r.hgvs_c, hgvs_p=r.hgvs_p, frequency=None
                )
                for r in vv_results
            ]
            self._fill_frequencies(normalized_variants)

        return VariantResponse(
            meta=self._meta(),
            normalized=normalized_variants,
            error=None,
        )

    # ----- timing ----------------------------------------------------------

    @contextmanager
    def _timed(self, key: str) -> Iterator[None]:
        start = time.perf_counter_ns()
        try:
            yield
        finally:
            self._durations_ns[key] += time.perf_counter_ns() - start

    # ----- per-variant resolution to pseudo-VCFs ---------------------------

    def _resolve_via_vv(self, candidates: list[str]) -> "list[VVResult] | _PipelineError":
        vv_results: list[VVResult] = []
        errors: list[VariantValidatorError] = []
        for candidate in candidates:
            with self._timed("variantvalidator"):
                try:
                    vv_results.append(
                        self.vv_client.mane_select(_strip_uncertainty_parens(candidate))
                    )
                except VariantValidatorError as e:
                    errors.append(e)
        if vv_results:
            return vv_results
        # If any candidate hit a transient upstream timeout, surface as a
        # retriable code so the caller can re-issue once VV's per-gene cache
        # has warmed. NO_GENOMIC_COORDS is reserved for the genuine "VV
        # returned but has no GRCh38 mapping" case, which is not retriable.
        timeout = next((e for e in errors if e.code == "UPSTREAM_TIMEOUT"), None)
        if timeout:
            return _PipelineError(
                "VV_UPSTREAM_TIMEOUT", timeout.message, upstream="variantvalidator"
            )
        if not errors:
            return _PipelineError(
                "NO_GENOMIC_COORDS", "no candidates resolved", upstream="variantvalidator"
            )
        last = errors[-1]
        return _PipelineError(
            "NO_GENOMIC_COORDS",
            f"{last.code}: {last.message}",
            upstream="variantvalidator",
        )

    def _cleanup_and_normalize(self, variant: VariantInput, genome_build: str | None) -> list[str]:
        """Apply rsID lookup / text cleanup / Mutalyzer normalization / back-translation."""
        cleaned = self._to_cleaned_variant(variant, genome_build)
        with self._timed("normalize"):
            try:
                normalized = self.mutalyzer_client.normalize(str(cleaned))
            except MutalyzerError as e:
                raise _PipelineError(
                    code=f"NORMALIZATION_{e.code}",
                    message=e.message,
                    upstream="mutalyzer",
                ) from e
        normalized_str = normalized.get("normalized_description") or str(cleaned)

        if ":p." not in normalized_str:
            return [normalized_str]

        with self._timed("back_translate"):
            try:
                return self.mutalyzer_client.back_translate(normalized_str)
            except MutalyzerError as e:
                raise _PipelineError(
                    code=f"BACK_TRANSLATE_{e.code}", message=e.message, upstream="mutalyzer"
                ) from e

    def _to_cleaned_variant(
        self, variant: VariantInput, genome_build: str | None
    ) -> normalize.CleanedVariant:
        rsid = normalize.extract_rsid(variant.variant)
        if rsid:
            with self._timed("rsid"):
                try:
                    resolution = ncbi.resolve_rsid(
                        rsid,
                        email=self.settings.ncbi_eutils_email,
                        api_key=self.settings.ncbi_eutils_api_key,
                    )
                except ncbi.NCBIError as e:
                    raise _PipelineError(
                        code=f"RSID_{e.code}", message=e.message, upstream="ncbi"
                    ) from e
            hgvs_str = resolution.hgvs_c or resolution.hgvs_p or resolution.hgvs_g
            if not hgvs_str or ":" not in hgvs_str:
                raise _PipelineError(
                    code="RSID_UNRESOLVED",
                    message=f"NCBI returned no HGVS for {rsid}",
                    upstream="ncbi",
                )
            refseq, hgvs_desc = hgvs_str.split(":", 1)
            return normalize.CleanedVariant(refseq=refseq, hgvs_desc=hgvs_desc)

        with self._timed("cleanup"):
            try:
                return normalize.clean(
                    variant.variant, variant.gene, genome_build, self.refseq_index
                )
            except normalize.VariantCleanupError as e:
                raise _PipelineError(code="VARIANT_CLEANUP_FAILED", message=str(e)) from e

    # ----- frequency lookup ------------------------------------------------

    def _fill_frequencies(self, normalized_variants: list[NormalizedVariant]) -> None:
        if not normalized_variants:
            return
        pseudo_vcfs = [nv.pseudo_vcf for nv in normalized_variants]
        with self._timed("echtvar"):
            frequencies = echtvar.annotate(
                pseudo_vcfs,
                archives_dir=self.settings.echtvar_archives_dir,
                gnomad_version=self.settings.gnomad_version,
                binary=self.settings.echtvar_bin,
            )
        for i, freq in enumerate(frequencies):
            normalized_variants[i] = normalized_variants[i].model_copy(update={"frequency": freq})

    # ----- response builders -----------------------------------------------

    def _meta(self) -> ResponseMeta:
        return ResponseMeta(
            service=self.settings.service_version or __version__,
            reference="GRCh38",
            gnomad=self.settings.gnomad_version,
            variantvalidator=versions.variantvalidator_version(self.settings.vv_base_url),
            mutalyzer=versions.mutalyzer_version(self.settings.mutalyzer_base_url),
            timestamp=datetime.datetime.now(tz=datetime.UTC).isoformat(),
            durations_ms={
                stage: round(self._durations_ns.get(stage, 0) / 1_000_000) for stage in _STAGES
            },
        )

    def _error_response(self, error: "_PipelineError") -> VariantResponse:
        return VariantResponse(
            meta=self._meta(),
            normalized=None,
            error=VariantError(code=error.code, upstream=error.upstream, message=error.message),
        )


# ----- helpers -------------------------------------------------------------


class _PipelineError(Exception):
    def __init__(self, code: str, message: str, *, upstream: str | None = None) -> None:
        self.code = code
        self.message = message
        self.upstream = upstream
        super().__init__(f"{code}: {message}")


def _strip_uncertainty_parens(hgvs: str) -> str:
    """Strip uncertainty parens (``c.(127G>T)``) from the *description* side.

    Mutalyzer's back-translate output sometimes carries uncertainty parens
    around the change; VV rejects them. But parens are also load-bearing
    on the *refseq* side for intronic-wrapped descriptions like
    ``NC_chr(NM_X):c.…+N`` — stripping those mangles the refseq into
    ``NC_chrNM_X`` which VV can't parse. Only touch the part after the
    first ``:``.
    """
    if ":" not in hgvs:
        return hgvs.replace("(", "").replace(")", "")
    refseq, desc = hgvs.split(":", 1)
    return f"{refseq}:{desc.replace('(', '').replace(')', '')}"
