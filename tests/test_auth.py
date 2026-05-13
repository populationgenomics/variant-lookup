"""API-key authentication tests."""

from fastapi.testclient import TestClient

from tests.conftest import TEST_BEARER, TEST_KEY_NAME
from variant_lookup import auth
from variant_lookup.api import create_app


def _client() -> TestClient:
    auth.clear_cache()
    return TestClient(create_app())


def _request(client: TestClient, **kwargs: object) -> int:
    return client.post(
        "/v1/variants",
        json={"genome_build": "GRCh38", "variants": []},
        **kwargs,  # type: ignore[arg-type]
    ).status_code


def test_missing_header_rejected() -> None:
    assert _request(_client()) == 401


def test_non_bearer_scheme_rejected() -> None:
    assert _request(_client(), headers={"Authorization": "Basic abc"}) == 401


def test_token_without_name_separator_rejected() -> None:
    assert _request(_client(), headers={"Authorization": "Bearer plainsecret"}) == 401


def test_unknown_caller_name_rejected() -> None:
    headers = {"Authorization": "Bearer nobody.secret"}
    assert _request(_client(), headers=headers) == 401


def test_correct_name_wrong_secret_rejected() -> None:
    headers = {"Authorization": f"Bearer {TEST_KEY_NAME}.wrong-secret"}
    assert _request(_client(), headers=headers) == 401


def test_valid_credentials_with_empty_batch_succeeds() -> None:
    headers = {"Authorization": f"Bearer {TEST_BEARER}"}
    response = _client().post(
        "/v1/variants",
        json={"genome_build": "GRCh38", "variants": []},
        headers=headers,
    )
    assert response.status_code == 200
