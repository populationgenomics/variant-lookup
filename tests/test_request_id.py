"""Tests for the per-request X-Request-ID middleware."""

import uuid

from fastapi.testclient import TestClient

from variant_lookup.api import create_app


def test_response_carries_generated_request_id() -> None:
    client = TestClient(create_app())
    response = client.get("/healthz")
    request_id = response.headers["X-Request-ID"]
    # Must be a parseable UUID — the middleware mints one when the caller
    # didn't supply X-Request-ID.
    uuid.UUID(request_id)


def test_supplied_request_id_is_echoed_back() -> None:
    client = TestClient(create_app())
    supplied = "00000000-1111-2222-3333-444444444444"
    response = client.get("/healthz", headers={"X-Request-ID": supplied})
    assert response.headers["X-Request-ID"] == supplied
