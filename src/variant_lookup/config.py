"""Environment-driven configuration. All deployment-specific values come from env vars."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    api_keys_file: Path = Field(
        ...,
        description="Path to YAML file with argon2id-hashed API keys (mounted from outside).",
    )

    vv_base_url: str = Field(
        ...,
        description="Base URL of the sibling VariantValidator container (HTTP only).",
    )
    echtvar_bin: str = Field("echtvar", description="echtvar binary, expected on PATH.")
    echtvar_archive: Path = Field(
        ...,
        description="Path to the encoded gnomAD echtvar archive inside the container.",
    )
    ncbi_eutils_email: str = Field(..., description="Required by NCBI E-utils.")
    ncbi_eutils_api_key: str | None = Field(
        None,
        description="Optional NCBI API key; raises rate limit from 3 to 10 req/sec.",
    )

    service_version: str = "0.1.0+dev"
    gnomad_version: str = "4.1"
    variantvalidator_version: str = "unknown"
    mutalyzer_version: str = "unknown"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # populated from env / .env
