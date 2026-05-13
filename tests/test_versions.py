"""Tests for runtime version discovery."""

import respx
from httpx import Response

from variant_lookup import versions


def test_mutalyzer_version_resolved_from_metadata() -> None:
    """importlib.metadata returns mutalyzer's installed version (a PEP 440 string)."""
    versions.mutalyzer_version.cache_clear()
    result = versions.mutalyzer_version()
    # Don't pin to an exact version — just that it isn't the fallback.
    assert result != "unknown"
    assert result  # non-empty
    # PEP 440 — first character is a digit
    assert result[0].isdigit()


@respx.mock
def test_variantvalidator_version_parses_hello_banner() -> None:
    base = "http://variantvalidator:8000"
    respx.get(f"{base}/hello").mock(
        return_value=Response(
            200,
            json={
                "metadata": {
                    "variantvalidator_version": "3.0.2.dev235+ge5bb05951",
                    "vvdb_version": "vvdb_2025_3",
                },
                "status": "hello_world",
            },
        )
    )
    versions.variantvalidator_version.cache_clear()
    assert versions.variantvalidator_version(base) == "3.0.2.dev235+ge5bb05951"


@respx.mock
def test_variantvalidator_version_unknown_on_http_error() -> None:
    base = "http://variantvalidator-down:8000"
    respx.get(f"{base}/hello").mock(return_value=Response(500))
    versions.variantvalidator_version.cache_clear()
    assert versions.variantvalidator_version(base) == "unknown"


@respx.mock
def test_variantvalidator_version_unknown_on_missing_field() -> None:
    base = "http://variantvalidator-weird:8000"
    respx.get(f"{base}/hello").mock(return_value=Response(200, json={"status": "ok"}))
    versions.variantvalidator_version.cache_clear()
    assert versions.variantvalidator_version(base) == "unknown"


@respx.mock
def test_variantvalidator_version_is_cached() -> None:
    base = "http://variantvalidator-cached:8000"
    route = respx.get(f"{base}/hello").mock(
        return_value=Response(200, json={"metadata": {"variantvalidator_version": "1.2.3"}})
    )
    versions.variantvalidator_version.cache_clear()
    assert versions.variantvalidator_version(base) == "1.2.3"
    assert versions.variantvalidator_version(base) == "1.2.3"
    assert route.call_count == 1
