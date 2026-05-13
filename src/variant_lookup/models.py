"""Request and response schemas for /v1/variants. See ARCHITECTURE.md § 'Public API'."""

from pydantic import BaseModel, Field


class VariantInput(BaseModel):
    id: str = Field(..., description="Caller-supplied identifier echoed back in the response.")
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


class VariantBatchRequest(BaseModel):
    genome_build: str = Field(
        ...,
        description="GRCh38 or GRCh37 (latter is projected to GRCh38 via VV).",
    )
    variants: list[VariantInput] = Field(..., max_length=1000)


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


class VariantResult(BaseModel):
    id: str
    input: VariantInput
    normalized: list[NormalizedVariant] | None
    error: VariantError | None


class ResponseMeta(BaseModel):
    """Top-level metadata stamped into every ``/v1/variants`` response.

    ``durations_ms`` reports wall-clock time spent in each pipeline stage
    (summed across all variants in the batch). Keys: ``cleanup``, ``rsid``,
    ``normalize``, ``back_translate``, ``variantvalidator``, ``echtvar``,
    ``total``. Stages that did not fire for this batch report ``0``.
    """

    service: str
    reference: str = "GRCh38"
    gnomad: str
    variantvalidator: str
    mutalyzer: str
    timestamp: str
    durations_ms: dict[str, int]


class VariantBatchResponse(BaseModel):
    meta: ResponseMeta
    results: list[VariantResult]


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
