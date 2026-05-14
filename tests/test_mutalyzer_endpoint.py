"""Tests for the /mutalyzer/* passthrough endpoints."""

from unittest.mock import patch

from fastapi.testclient import TestClient

from tests.conftest import TEST_BEARER
from variant_lookup.api import create_app
from variant_lookup.mutalyzer_client import MutalyzerError


def test_normalize_requires_auth() -> None:
    client = TestClient(create_app())
    response = client.get("/mutalyzer/normalize/NM_003002.2:c.274G%3ET")
    assert response.status_code == 401


def test_normalize_returns_library_dict() -> None:
    fake_response = {
        "normalized_description": "NM_003002.2:c.274G>T",
        "protein": {"description": "..."},
    }
    # Patch the MutalyzerClient class so instantiation in the route returns
    # a mock whose .normalize_raw returns our fake.
    with patch("variant_lookup.api.MutalyzerClient") as MC:
        MC.return_value.normalize_raw.return_value = fake_response
        client = TestClient(create_app())
        response = client.get(
            "/mutalyzer/normalize/NM_003002.2:c.274G%3ET",
            headers={"Authorization": f"Bearer {TEST_BEARER}"},
        )
    assert response.status_code == 200
    assert response.json() == fake_response
    # Path param decoding: the `>` is %3E-encoded by the client.
    MC.return_value.normalize_raw.assert_called_once_with("NM_003002.2:c.274G>T")


def test_back_translate_requires_auth() -> None:
    client = TestClient(create_app())
    response = client.get("/mutalyzer/back_translate/NP_002993.1:p.Asp92Glu")
    assert response.status_code == 401


def test_back_translate_returns_list() -> None:
    fake_list = ["NM_003002.4:c.(276C>G)", "NM_003002.4:c.(276C>A)"]
    with patch("variant_lookup.api.MutalyzerClient") as MC:
        MC.return_value.back_translate.return_value = fake_list
        client = TestClient(create_app())
        response = client.get(
            "/mutalyzer/back_translate/NP_002993.1:p.Asp92Glu",
            headers={"Authorization": f"Bearer {TEST_BEARER}"},
        )
    assert response.status_code == 200
    assert response.json() == fake_list


def test_back_translate_frameshift_returns_422() -> None:
    with patch("variant_lookup.api.MutalyzerClient") as MC:
        MC.return_value.back_translate.side_effect = MutalyzerError("FRAMESHIFT_UNSUPPORTED", "no.")
        client = TestClient(create_app())
        response = client.get(
            "/mutalyzer/back_translate/NP_002993.1:p.Arg100fs",
            headers={"Authorization": f"Bearer {TEST_BEARER}"},
        )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "FRAMESHIFT_UNSUPPORTED"
