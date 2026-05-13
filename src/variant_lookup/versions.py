"""Runtime version discovery for components stamped into response `meta`.

Mutalyzer is pip-installed in the gateway image, so we resolve it via
`importlib.metadata`. VariantValidator runs as a sibling container; its
`/hello` endpoint exposes the deployed version (which is unstable because
upstream's pyproject.toml pins to master — see ARCHITECTURE.md § "What runs
where"). We capture VV's version on first access and cache it; restart the
gateway to re-detect after a VV redeploy.
"""

import functools
import importlib.metadata
import logging

import httpx

logger = logging.getLogger(__name__)

_VV_BANNER_TIMEOUT_S = 5.0
_UNKNOWN = "unknown"


@functools.lru_cache(maxsize=1)
def mutalyzer_version() -> str:
    try:
        return importlib.metadata.version("mutalyzer")
    except importlib.metadata.PackageNotFoundError:
        logger.warning("mutalyzer package metadata not found; reporting version as 'unknown'")
        return _UNKNOWN


@functools.lru_cache(maxsize=4)
def variantvalidator_version(base_url: str) -> str:
    """Fetch and cache the VV-reported version from its `/hello` banner.

    Returns ``"unknown"`` on any error so a degraded VV can't break our
    response shape. lru_cache is keyed on base_url, so the typical
    single-deployment case caches once after the first request.
    """
    url = f"{base_url.rstrip('/')}/hello"
    try:
        response = httpx.get(url, timeout=_VV_BANNER_TIMEOUT_S)
        response.raise_for_status()
        version = response.json().get("metadata", {}).get("variantvalidator_version")
        if not isinstance(version, str):
            logger.warning("VV /hello returned no variantvalidator_version field")
            return _UNKNOWN
        return version
    except httpx.HTTPError as e:
        logger.warning("Could not fetch VV version from %s: %s", url, e)
        return _UNKNOWN
