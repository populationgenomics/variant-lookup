"""Health and readiness endpoints — see ARCHITECTURE.md § 'Public API'."""

from typing import Any

import httpx

from variant_lookup.config import Settings

_PROBE_TIMEOUT_S = 2.0


def healthz() -> dict[str, str]:
    return {"status": "ok"}


def _probe_file(path: object) -> dict[str, str]:
    from pathlib import Path

    p = Path(str(path))
    if not p.exists():
        return {"status": "missing", "path": str(p)}
    if p.is_dir() or p.stat().st_size == 0:
        return {"status": "empty", "path": str(p)}
    return {"status": "ok", "path": str(p)}


def _probe_vv(base_url: str) -> dict[str, str]:
    try:
        response = httpx.get(base_url, timeout=_PROBE_TIMEOUT_S)
    except httpx.HTTPError as e:
        return {"status": "unreachable", "error": str(e)}
    if response.is_success or response.is_redirect:
        return {"status": "ok", "http": str(response.status_code)}
    return {"status": "unhealthy", "http": str(response.status_code)}


def readyz(settings: Settings) -> dict[str, Any]:
    upstreams = {
        "echtvar_archive": _probe_file(settings.echtvar_archive),
        "refseq_cache": _probe_file(settings.refseq_cache_path),
        "variantvalidator": _probe_vv(settings.vv_base_url),
    }
    overall = "ready" if all(u["status"] == "ok" for u in upstreams.values()) else "degraded"
    return {"status": overall, "upstreams": upstreams}
