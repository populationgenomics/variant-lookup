"""Request and response schemas for /v1/variant. See ARCHITECTURE.md § 'Public API'."""

from pydantic import BaseModel, Field


class VariantInput(BaseModel):
    variant: str = Field(..., description="HGVS-like description or `rs…` rsID.")
    gene: str | None = Field(
        None,
        description=(
            "HGNC gene symbol. Required when `variant` is unqualified (bare "
            "`c.…` / `p.…` without a RefSeq prefix, or a `GENE:c.…` form). "
            "Optional when `variant` is fully qualified (e.g. "
            "`NC_000016.10:g.…`, `NM_006749.5:c.…`) or an rsID."
        ),
    )


class VariantRequest(VariantInput):
    """Body shape of ``POST /v1/variant``.

    Same fields as ``VariantInput`` plus ``genome_build``, which is per-request
    rather than per-variant — placed at the body root for ergonomics.
    """

    genome_build: str | None = Field(
        None,
        description=(
            "GRCh37 or GRCh38 (or ``hg19`` / ``hg38`` aliases). Required only "
            "when ``variant`` is a chromosomal refseq (e.g. ``chr17:g.…``); "
            "ignored for inputs with fully-qualified RefSeq accessions "
            "(``NM_…``, ``NP_…``, ``NC_…``) or rsIDs (latter is projected to "
            "GRCh38 via VV)."
        ),
    )


class Frequency(BaseModel):
    """gnomAD-derived counts and filtering-AF for a single pseudo-VCF.

    Counts:
      - ``ac``: total alt-allele count.
      - ``an``: total called allele count.
      - ``homozygote_count``: individuals homozygous for the alt allele.
      - ``heterozygote_count``: individuals heterozygous for the alt allele
        (derived: ``max(ac - 2*homozygote_count - hemizygote_count, 0)``).
      - ``hemizygote_count``: hemizygous individuals. ``0`` on autosomes and
        on the chrX/Y PAR regions — only meaningful on non-PAR chrX/Y.
        Always emitted for response-shape stability.
    """

    ac: int
    an: int
    homozygote_count: int
    heterozygote_count: int
    hemizygote_count: int
    faf95_popmax: float | None
    faf95_popmax_population: str | None


class NormalizedVariant(BaseModel):
    pseudo_vcf: str
    hgvs_c: str | None
    hgvs_p: str | None
    frequency: Frequency | None


class VariantError(BaseModel):
    code: str
    upstream: str | None = None
    message: str


class ResponseMeta(BaseModel):
    """Top-level metadata stamped into every ``POST /v1/variant`` response.

    ``durations_ms`` reports wall-clock time spent in each pipeline stage for
    this request. Keys: ``cleanup``, ``rsid``, ``normalize``,
    ``back_translate``, ``variantvalidator``, ``echtvar``, ``total``. Stages
    that did not fire for this request report ``0``.
    """

    service: str
    reference: str = "GRCh38"
    gnomad: str
    variantvalidator: str
    mutalyzer: str
    timestamp: str
    durations_ms: dict[str, int]


class VariantResponse(BaseModel):
    """Body shape returned by ``POST /v1/variant``."""

    meta: ResponseMeta
    normalized: list[NormalizedVariant] | None
    error: VariantError | None


# --- /echtvar/frequencies passthrough -------------------------------------
# Unstable contract; mirrors the gnomAD-frequency half of the stable pipeline
# for callers that already have pseudo-VCFs. See ARCHITECTURE.md § "Passthrough".


class EchtvarFrequenciesRequest(BaseModel):
    variants: list[str] = Field(
        ...,
        max_length=1000,
        description="Pseudo-VCF strings, e.g. '8-42437272-C-A' (GRCh38, no chr prefix).",
    )


class EchtvarResult(BaseModel):
    pseudo_vcf: str
    frequency: Frequency | None


class EchtvarFrequenciesResponse(BaseModel):
    meta: dict[str, str]
    results: list[EchtvarResult]
