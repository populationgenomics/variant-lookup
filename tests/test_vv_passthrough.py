"""Tests for the /variantvalidator/* passthrough endpoint (respx-mocked upstream)."""

import httpx
import respx
from fastapi.testclient import TestClient

from tests.conftest import TEST_BEARER
from variant_lookup.api import create_app
from variant_lookup.config import get_settings


def test_passthrough_requires_auth() -> None:
    client = TestClient(create_app())
    response = client.get(
        "/variantvalidator/VariantValidator/variantvalidator/GRCh38/whatever/mane_select"
    )
    assert response.status_code == 401


@respx.mock
def test_passthrough_forwards_to_upstream_and_returns_payload() -> None:
    settings = get_settings()
    upstream_body = '{"some": "result"}'
    upstream_url = (
        f"{settings.vv_base_url.rstrip('/')}/"
        f"VariantValidator/variantvalidator/GRCh38/NM_000551.3%3Ac.1582G%3EA/mane_select"
    )
    route = respx.get(upstream_url).mock(
        return_value=httpx.Response(
            200,
            content=upstream_body.encode(),
            headers={"content-type": "application/json"},
        )
    )

    client = TestClient(create_app())
    response = client.get(
        "/variantvalidator/VariantValidator/variantvalidator/GRCh38/NM_000551.3%3Ac.1582G%3EA/mane_select",
        headers={"Authorization": f"Bearer {TEST_BEARER}"},
    )

    assert route.called
    assert response.status_code == 200
    assert response.text == upstream_body
    assert response.headers["content-type"] == "application/json"


@respx.mock
def test_passthrough_propagates_upstream_error_status() -> None:
    settings = get_settings()
    respx.get(f"{settings.vv_base_url.rstrip('/')}/some/path").mock(
        return_value=httpx.Response(503, content=b"overloaded")
    )

    client = TestClient(create_app())
    response = client.get(
        "/variantvalidator/some/path",
        headers={"Authorization": f"Bearer {TEST_BEARER}"},
    )
    assert response.status_code == 503
