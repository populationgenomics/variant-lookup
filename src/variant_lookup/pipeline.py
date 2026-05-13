"""Per-variant pipeline orchestrator.

Wires the components landed in Phases 1-5 into the end-to-end chain that
``POST /v1/variants`` exposes:

1. parse + clean the raw text (or resolve an rsID via NCBI)
2. normalize via Mutalyzer
3. for ``p.`` inputs, back-translate to a list of coding-variant candidates
4. for each candidate, ask VV for the GRCh38 pseudo-VCF + MANE-select hgvs-c/p
5. bulk-lookup frequencies once via echtvar
6. assemble per-variant :class:`VariantResult` objects in request order
"""

import datetime
from dataclasses import dataclass

from variant_lookup import __version__, echtvar, mutalyzer_client, ncbi, normalize
from variant_lookup.config import Settings
from variant_lookup.models import (
    NormalizedVariant,
    ResponseMeta,
    VariantBatchResponse,
    VariantError,
    VariantInput,
    VariantResult,
)
from variant_lookup.refseq import RefSeqIndex
from variant_lookup.variantvalidator_client import (
    VariantValidatorClient,
    VariantValidatorError,
    VVResult,
)


@dataclass
class Pipeline:
    settings: Settings
    refseq_index: RefSeqIndex
    vv_client: VariantValidatorClient

    def process_batch(
        self, variants: list[VariantInput], genome_build: str
    ) -> VariantBatchResponse:
        results = [self._resolve_one(v, genome_build) for v in variants]
        self._fill_frequencies(results)
        return VariantBatchResponse(meta=self._meta(), results=results)

    # ----- per-variant resolution to pseudo-VCFs ---------------------------

    def _resolve_one(self, variant: VariantInput, genome_build: str) -> VariantResult:
        try:
            normalized_hgvs_strings = self._cleanup_and_normalize(variant, genome_build)
        except _PipelineError as e:
            return _error_result(variant, e)

        vv_results: list[VVResult] = []
        last_error: VariantValidatorError | None = None
        for candidate in normalized_hgvs_strings:
            try:
                vv_results.append(self.vv_client.mane_select(_strip_parens(candidate)))
            except VariantValidatorError as e:
                last_error = e

        if not vv_results:
            message = (
                f"{last_error.code}: {last_error.message}"
                if last_error
                else "no candidates resolved"
            )
            return _error_result(
                variant,
                _PipelineError("NO_GENOMIC_COORDS", message, upstream="variantvalidator"),
            )

        return VariantResult(
            id=variant.id,
            input=variant,
            normalized=[
                NormalizedVariant(
                    pseudo_vcf=r.pseudo_vcf, hgvs_c=r.hgvs_c, hgvs_p=r.hgvs_p, frequency=None
                )
                for r in vv_results
            ],
            error=None,
        )

    def _cleanup_and_normalize(self, variant: VariantInput, genome_build: str) -> list[str]:
        """Apply rsID lookup / text cleanup / Mutalyzer normalization / back-translation."""
        cleaned = self._to_cleaned_variant(variant, genome_build)
        try:
            normalized = mutalyzer_client.normalize(str(cleaned))
        except mutalyzer_client.MutalyzerError as e:
            raise _PipelineError(
                code=f"NORMALIZATION_{e.code}",
                message=e.message,
                upstream="mutalyzer",
            ) from e
        normalized_str = normalized.get("normalized_description") or str(cleaned)

        if ":p." not in normalized_str:
            return [normalized_str]

        try:
            return mutalyzer_client.back_translate(normalized_str)
        except mutalyzer_client.MutalyzerError as e:
            raise _PipelineError(
                code=f"BACK_TRANSLATE_{e.code}", message=e.message, upstream="mutalyzer"
            ) from e

    def _to_cleaned_variant(
        self, variant: VariantInput, genome_build: str
    ) -> normalize.CleanedVariant:
        rsid = normalize.extract_rsid(variant.variant)
        if rsid:
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

        try:
            return normalize.clean(variant.variant, variant.gene, genome_build, self.refseq_index)
        except normalize.VariantCleanupError as e:
            raise _PipelineError(code="VARIANT_CLEANUP_FAILED", message=str(e)) from e

    # ----- bulk frequency lookup -------------------------------------------

    def _fill_frequencies(self, results: list[VariantResult]) -> None:
        positions: list[tuple[int, int]] = []
        pseudo_vcfs: list[str] = []
        for ri, result in enumerate(results):
            if not result.normalized:
                continue
            for ni, nv in enumerate(result.normalized):
                positions.append((ri, ni))
                pseudo_vcfs.append(nv.pseudo_vcf)

        if not pseudo_vcfs:
            return

        frequencies = echtvar.annotate(
            pseudo_vcfs,
            archives_dir=self.settings.echtvar_archives_dir,
            gnomad_version=self.settings.gnomad_version,
            binary=self.settings.echtvar_bin,
        )
        for (ri, ni), freq in zip(positions, frequencies, strict=True):
            existing = results[ri].normalized
            assert existing is not None  # mypy: positions only added when not None
            existing[ni] = existing[ni].model_copy(update={"frequency": freq})

    # ----- response meta ---------------------------------------------------

    def _meta(self) -> ResponseMeta:
        return ResponseMeta(
            service=self.settings.service_version or __version__,
            reference="GRCh38",
            gnomad=self.settings.gnomad_version,
            variantvalidator=self.settings.variantvalidator_version,
            mutalyzer=self.settings.mutalyzer_version,
            timestamp=datetime.datetime.now(tz=datetime.UTC).isoformat(),
        )


# ----- helpers -------------------------------------------------------------


class _PipelineError(Exception):
    def __init__(self, code: str, message: str, *, upstream: str | None = None) -> None:
        self.code = code
        self.message = message
        self.upstream = upstream
        super().__init__(f"{code}: {message}")


def _strip_parens(hgvs: str) -> str:
    """Mutalyzer's back-translate output sometimes carries uncertainty parens;
    VV rejects them."""
    return hgvs.replace("(", "").replace(")", "")


def _error_result(variant: VariantInput, error: "_PipelineError") -> VariantResult:
    return VariantResult(
        id=variant.id,
        input=variant,
        normalized=None,
        error=VariantError(code=error.code, upstream=error.upstream, message=error.message),
    )
