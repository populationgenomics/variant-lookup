"""Smoke tests — app boots, basic routes respond, auth gate is wired up."""

from fastapi.testclient import TestClient

from variant_lookup.api import create_app


def test_healthz() -> None:
    client = TestClient(create_app())
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_openapi_exposed() -> None:
    client = TestClient(create_app())
    assert client.get("/openapi.json").status_code == 200


def test_variants_requires_auth() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/v1/variants",
        json={"genome_build": "GRCh38", "variants": []},
    )
    assert response.status_code == 401
