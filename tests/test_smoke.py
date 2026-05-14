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


def test_variant_requires_auth() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/v1/variant",
        json={"genome_build": "GRCh38", "variant": "12345"},
    )
    assert response.status_code == 401
