"""Request and response schemas for /v1/variants. See ARCHITECTURE.md § 'Public API'."""

from pydantic import BaseModel, Field


class VariantInput(BaseModel):
    id: str = Field(..., description="Caller-supplied identifier echoed back in the response.")
    gene: str
    hgnc_id: int
    variant: str


class VariantBatchRequest(BaseModel):
    genome_build: str = Field(
        ...,
        description="GRCh38 or GRCh37 (latter is projected to GRCh38 via VV).",
    )
    variants: list[VariantInput] = Field(..., max_length=1000)


class Frequency(BaseModel):
    ac: int
    an: int
    homozygote_count: int
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
    service: str
    reference: str = "GRCh38"
    gnomad: str
    variantvalidator: str
    mutalyzer: str
    timestamp: str


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
