"""Health and readiness probe tests."""

from fastapi.testclient import TestClient

from variant_lookup.api import create_app


def test_healthz_ok() -> None:
    client = TestClient(create_app())
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_degraded_when_upstreams_missing() -> None:
    # conftest leaves echtvar / refseq files non-existent and VV URL unreachable.
    client = TestClient(create_app())
    response = client.get("/readyz")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["upstreams"]["echtvar_archive"]["status"] == "missing"
    assert body["upstreams"]["refseq_cache"]["status"] == "missing"
    assert body["upstreams"]["variantvalidator"]["status"] == "unreachable"
