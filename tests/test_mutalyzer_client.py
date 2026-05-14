"""Tests for the mutalyzer-api HTTP client wrapper."""

import pytest
import respx
from httpx import Response

from variant_lookup.mutalyzer_client import MutalyzerClient, MutalyzerError

_BASE = "http://mutalyzer-api.invalid:5000"
_OK_RESPONSE = {
    "normalized_description": "NM_003002.2:c.274G>T",
    "protein": {"description": "NM_003002.2(NP_002993.1):p.(Asp92Tyr)"},
    "equivalent_descriptions": [{"c": "NM_003002.2:c.274G>T"}],
    "infos": [{"code": "IMRNAGENOMICTIP"}],
    "gene_id": "SDHD",
}


@pytest.fixture
def client() -> MutalyzerClient:
    return MutalyzerClient(_BASE)


@respx.mock
def test_normalize_raw_returns_upstream_response(client: MutalyzerClient) -> None:
    respx.get(f"{_BASE}/api/normalize/NM_003002.2%3Ac.274G%3ET").mock(
        return_value=Response(200, json=_OK_RESPONSE)
    )
    assert client.normalize_raw("NM_003002.2:c.274G>T") == _OK_RESPONSE


@respx.mock
def test_normalize_trims_to_pipeline_fields(client: MutalyzerClient) -> None:
    respx.get(f"{_BASE}/api/normalize/NM_003002.2%3Ac.274G%3ET").mock(
        return_value=Response(200, json=_OK_RESPONSE)
    )
    assert client.normalize("NM_003002.2:c.274G>T") == {
        "normalized_description": "NM_003002.2:c.274G>T",
        "protein": {"description": "NM_003002.2(NP_002993.1):p.(Asp92Tyr)"},
        "equivalent_descriptions": [{"c": "NM_003002.2:c.274G>T"}],
    }


@respx.mock
def test_normalize_raises_on_top_level_errors(client: MutalyzerClient) -> None:
    error_response = {
        "errors": [{"code": "EREF", "details": "reference sequence not found"}],
    }
    respx.get(f"{_BASE}/api/normalize/NM_999999.9%3Ac.1A%3ET").mock(
        return_value=Response(200, json=error_response)
    )
    with pytest.raises(MutalyzerError) as exc:
        client.normalize("NM_999999.9:c.1A>T")
    assert exc.value.code == "EREF"


@respx.mock
def test_normalize_raises_on_custom_errors(client: MutalyzerClient) -> None:
    error_response = {"custom": {"errors": [{"code": "ESYNTAX", "details": "bad input"}]}}
    respx.get(f"{_BASE}/api/normalize/garbage").mock(
        return_value=Response(200, json=error_response)
    )
    with pytest.raises(MutalyzerError) as exc:
        client.normalize("garbage")
    assert exc.value.code == "ESYNTAX"


@respx.mock
def test_normalize_raises_on_http_error(client: MutalyzerClient) -> None:
    respx.get(f"{_BASE}/api/normalize/x").mock(return_value=Response(500))
    with pytest.raises(MutalyzerError) as exc:
        client.normalize("x")
    assert exc.value.code == "UPSTREAM_ERROR"


def test_frameshift_skips_http_and_canonicalises(client: MutalyzerClient) -> None:
    # Frameshift inputs short-circuit before hitting mutalyzer-api.
    # respx not mocked here — would raise if the code tried to make a real call.
    result = client.normalize_raw("NM_006749.5:p.(R191fs)")
    assert result == {"normalized_description": "NM_006749.5:p.(Arg191fs)"}


def test_frameshift_three_letter_input_preserved(client: MutalyzerClient) -> None:
    result = client.normalize_raw("NM_006749.5:p.Arg191GlyfsTer5")
    assert result == {"normalized_description": "NM_006749.5:p.Arg191fs"}


@respx.mock
def test_back_translate_returns_list(client: MutalyzerClient) -> None:
    respx.get(f"{_BASE}/api/back_translate/NP_002993.1%3Ap.Asp92Glu").mock(
        return_value=Response(200, json=["NM_003002.4:c.(276C>G)", "NM_003002.4:c.(276C>A)"])
    )
    result = client.back_translate("NP_002993.1:p.Asp92Glu")
    assert result == ["NM_003002.4:c.(276C>G)", "NM_003002.4:c.(276C>A)"]


def test_back_translate_frameshift_rejected(client: MutalyzerClient) -> None:
    with pytest.raises(MutalyzerError) as exc:
        client.back_translate("NP_002993.1:p.Arg100GlyfsTer5")
    assert exc.value.code == "FRAMESHIFT_UNSUPPORTED"


@respx.mock
def test_back_translate_raises_on_http_error(client: MutalyzerClient) -> None:
    respx.get(f"{_BASE}/api/back_translate/p").mock(return_value=Response(500))
    with pytest.raises(MutalyzerError) as exc:
        client.back_translate("p")
    assert exc.value.code == "UPSTREAM_ERROR"
