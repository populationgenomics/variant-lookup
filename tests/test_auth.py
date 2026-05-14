"""API-key authentication tests."""

from unittest.mock import patch

from fastapi.testclient import TestClient

from tests.conftest import TEST_BEARER, TEST_KEY_NAME
from variant_lookup import auth
from variant_lookup.api import create_app


def _client() -> TestClient:
    auth.clear_cache()
    return TestClient(create_app())


def _post(client: TestClient, **kwargs: object) -> int:
    """Send a syntactically-valid body to /v1/variant. We only care about the
    auth path here, so we patch out the upstreams that would otherwise be
    contacted on a successful auth."""
    return client.post(
        "/v1/variant",
        json={"genome_build": "GRCh38", "variant": "12345"},
        **kwargs,  # type: ignore[arg-type]
    ).status_code


def test_missing_header_rejected() -> None:
    assert _post(_client()) == 401


def test_non_bearer_scheme_rejected() -> None:
    assert _post(_client(), headers={"Authorization": "Basic abc"}) == 401


def test_token_without_name_separator_rejected() -> None:
    assert _post(_client(), headers={"Authorization": "Bearer plainsecret"}) == 401


def test_unknown_caller_name_rejected() -> None:
    assert _post(_client(), headers={"Authorization": "Bearer nobody.secret"}) == 401


def test_correct_name_wrong_secret_rejected() -> None:
    headers = {"Authorization": f"Bearer {TEST_KEY_NAME}.wrong-secret"}
    assert _post(_client(), headers=headers) == 401


def test_valid_credentials_with_known_failing_input_returns_200() -> None:
    """Auth passes; the request body intentionally fails cleanup (unparseable
    "12345"), so we get a 200 with an error result — proves auth was accepted."""
    headers = {"Authorization": f"Bearer {TEST_BEARER}"}
    with (
        patch("variant_lookup.api.MutalyzerClient"),
        patch("variant_lookup.api.VariantValidatorClient"),
    ):
        response = _client().post(
            "/v1/variant",
            json={"genome_build": "GRCh38", "gene": "SLC20A2", "variant": "12345"},
            headers=headers,
        )
    assert response.status_code == 200
    assert response.json()["error"]["code"] == "VARIANT_CLEANUP_FAILED"
