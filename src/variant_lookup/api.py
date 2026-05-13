"""FastAPI application factory and route declarations."""

from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Response, status

from variant_lookup import __version__, echtvar, mutalyzer_client
from variant_lookup.auth import require_api_key
from variant_lookup.config import Settings, get_settings
from variant_lookup.health import healthz, readyz
from variant_lookup.logging_setup import configure_logging
from variant_lookup.models import (
    EchtvarFrequenciesRequest,
    EchtvarFrequenciesResponse,
    EchtvarResult,
    VariantBatchRequest,
    VariantBatchResponse,
)


def create_app() -> FastAPI:
    configure_logging()

    app = FastAPI(
        title="variant-lookup",
        version=__version__,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )

    @app.get("/healthz")
    def _healthz() -> dict[str, str]:
        return healthz()

    @app.get("/readyz")
    def _readyz(
        settings: Annotated[Settings, Depends(get_settings)],
        response: Response,
    ) -> dict[str, Any]:
        result = readyz(settings)
        if result["status"] != "ready":
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return result

    @app.post(
        "/v1/variants",
        response_model=VariantBatchResponse,
        dependencies=[Depends(require_api_key)],
    )
    def _lookup_variants(_request: VariantBatchRequest) -> VariantBatchResponse:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="variant lookup pipeline not yet implemented",
        )

    @app.get(
        "/mutalyzer/normalize/{description:path}",
        dependencies=[Depends(require_api_key)],
    )
    def _mutalyzer_normalize_passthrough(description: str) -> dict[str, Any]:
        return mutalyzer_client.normalize_raw(description)

    @app.get(
        "/mutalyzer/back_translate/{description:path}",
        dependencies=[Depends(require_api_key)],
    )
    def _mutalyzer_back_translate_passthrough(description: str) -> list[str]:
        try:
            return mutalyzer_client.back_translate(description)
        except mutalyzer_client.MutalyzerError as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": e.code, "message": e.message},
            ) from e

    @app.post(
        "/echtvar/frequencies",
        response_model=EchtvarFrequenciesResponse,
        dependencies=[Depends(require_api_key)],
    )
    def _echtvar_frequencies(
        request: EchtvarFrequenciesRequest,
        settings: Annotated[Settings, Depends(get_settings)],
    ) -> EchtvarFrequenciesResponse:
        frequencies = echtvar.annotate(
            request.variants,
            archive=settings.echtvar_archive,
            binary=settings.echtvar_bin,
        )
        return EchtvarFrequenciesResponse(
            meta={
                "service": settings.service_version,
                "reference": "GRCh38",
                "gnomad": settings.gnomad_version,
            },
            results=[
                EchtvarResult(pseudo_vcf=v, frequency=f)
                for v, f in zip(request.variants, frequencies, strict=True)
            ],
        )

    return app
