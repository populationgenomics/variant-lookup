"""Health and readiness endpoints — see ARCHITECTURE.md § 'Public API'."""

from pathlib import Path
from typing import Any

import httpx

from variant_lookup.config import Settings

_PROBE_TIMEOUT_S = 2.0

_EXPECTED_CHROMS: tuple[str, ...] = (*(str(i) for i in range(1, 23)), "X", "Y")


def healthz() -> dict[str, str]:
    return {"status": "ok"}


def _probe_file(path: object) -> dict[str, str]:
    p = Path(str(path))
    if not p.exists():
        return {"status": "missing", "path": str(p)}
    if p.is_dir() or p.stat().st_size == 0:
        return {"status": "empty", "path": str(p)}
    return {"status": "ok", "path": str(p)}


def _probe_echtvar_archives(archives_dir: Path, gnomad_version: str) -> dict[str, str]:
    if not archives_dir.exists():
        return {"status": "missing", "path": str(archives_dir)}
    if not archives_dir.is_dir():
        return {"status": "not_a_directory", "path": str(archives_dir)}
    missing: list[str] = []
    for chrom in _EXPECTED_CHROMS:
        p = archives_dir / f"gnomad.joint.v{gnomad_version}.chr{chrom}.echtvar.zip"
        if not p.is_file() or p.stat().st_size == 0:
            missing.append(chrom)
    if missing:
        return {
            "status": "incomplete",
            "path": str(archives_dir),
            "missing_chroms": ",".join(missing),
        }
    return {"status": "ok", "path": str(archives_dir)}


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
        "echtvar_archives": _probe_echtvar_archives(
            settings.echtvar_archives_dir, settings.gnomad_version
        ),
        "refseq_cache": _probe_file(settings.refseq_cache_path),
        "variantvalidator": _probe_vv(settings.vv_base_url),
    }
    overall = "ready" if all(u["status"] == "ok" for u in upstreams.values()) else "degraded"
    return {"status": overall, "upstreams": upstreams}
