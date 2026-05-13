"""FastAPI application factory and route declarations."""

from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Response, status

from variant_lookup import __version__
from variant_lookup.auth import require_api_key
from variant_lookup.config import Settings, get_settings
from variant_lookup.health import healthz, readyz
from variant_lookup.logging_setup import configure_logging
from variant_lookup.models import VariantBatchRequest, VariantBatchResponse


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

    return app
