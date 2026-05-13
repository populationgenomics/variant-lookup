"""FastAPI application factory and route declarations."""

from typing import Annotated, Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status

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
from variant_lookup.pipeline import Pipeline
from variant_lookup.refseq import get_index as get_refseq_index
from variant_lookup.variantvalidator_client import VariantValidatorClient


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
    def _lookup_variants(
        request: VariantBatchRequest,
        settings: Annotated[Settings, Depends(get_settings)],
    ) -> VariantBatchResponse:
        pipeline = Pipeline(
            settings=settings,
            refseq_index=get_refseq_index(),
            vv_client=VariantValidatorClient(settings.vv_base_url),
        )
        return pipeline.process_batch(request.variants, request.genome_build)

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

    @app.get(
        "/variantvalidator/{full_path:path}",
        dependencies=[Depends(require_api_key)],
    )
    def _vv_passthrough(
        full_path: str,
        request: Request,
        settings: Annotated[Settings, Depends(get_settings)],
    ) -> Response:
        """Drop-in proxy to the sibling VV REST service. Unstable contract."""
        upstream_url = f"{settings.vv_base_url.rstrip('/')}/{full_path}"
        with httpx.Client(timeout=60.0) as client:
            upstream = client.get(upstream_url, params=request.query_params)
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type"),
        )

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
