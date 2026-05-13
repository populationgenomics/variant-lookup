"""Health and readiness endpoints — see ARCHITECTURE.md § 'Public API'."""

from typing import Any

from variant_lookup.config import Settings


def healthz() -> dict[str, str]:
    return {"status": "ok"}


def readyz(settings: Settings) -> dict[str, Any]:
    return {
        "status": "ready",
        "upstreams": {
            "variantvalidator": "unknown",
            "echtvar_archive": {
                "status": "unknown",
                "path": str(settings.echtvar_archive),
            },
            "refseq_cache": "unknown",
        },
    }
