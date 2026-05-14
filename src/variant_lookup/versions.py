"""Runtime version discovery for components stamped into response `meta`.

Both Mutalyzer and VariantValidator now sit behind HTTP boundaries and expose
their library version via their own endpoints. Versions are captured on first
access and cached per-base-url; a redeploy of either upstream needs a gateway
restart for the new version to be reported (acceptable; redeploys are rare).
"""

import functools
import logging

import httpx

logger = logging.getLogger(__name__)

_BANNER_TIMEOUT_S = 5.0
_UNKNOWN = "unknown"


@functools.lru_cache(maxsize=4)
def mutalyzer_version(base_url: str) -> str:
    """Fetch and cache the mutalyzer library version from ``/api/version``.

    Returns ``"unknown"`` on any error so a degraded service can't break our
    response shape.
    """
    url = f"{base_url.rstrip('/')}/api/version"
    try:
        response = httpx.get(url, timeout=_BANNER_TIMEOUT_S)
        response.raise_for_status()
        version = response.json().get("mutalyzer")
        if not isinstance(version, str):
            logger.warning("mutalyzer-api /api/version returned no mutalyzer field")
            return _UNKNOWN
        return version
    except httpx.HTTPError as e:
        logger.warning("Could not fetch mutalyzer version from %s: %s", url, e)
        return _UNKNOWN


@functools.lru_cache(maxsize=4)
def variantvalidator_version(base_url: str) -> str:
    """Fetch and cache the VV-reported version from its ``/hello`` banner."""
    url = f"{base_url.rstrip('/')}/hello"
    try:
        response = httpx.get(url, timeout=_BANNER_TIMEOUT_S)
        response.raise_for_status()
        version = response.json().get("metadata", {}).get("variantvalidator_version")
        if not isinstance(version, str):
            logger.warning("VV /hello returned no variantvalidator_version field")
            return _UNKNOWN
        return version
    except httpx.HTTPError as e:
        logger.warning("Could not fetch VV version from %s: %s", url, e)
        return _UNKNOWN
