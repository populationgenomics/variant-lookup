"""Tests for the in-process Mutalyzer wrapper (mocked library calls)."""

import pytest

from variant_lookup import mutalyzer_client
from variant_lookup.mutalyzer_client import (
    MutalyzerError,
    back_translate,
    normalize,
    normalize_raw,
)

_OK_RESPONSE = {
    "normalized_description": "NM_003002.2:c.274G>T",
    "protein": {"description": "NM_003002.2(NP_002993.1):p.(Asp92Tyr)"},
    "equivalent_descriptions": [{"c": "NM_003002.2:c.274G>T"}],
    "infos": [{"code": "IMRNAGENOMICTIP"}],
    "gene_id": "SDHD",
}


def test_normalize_raw_returns_library_output_as_is(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mutalyzer_client, "_mt_normalize", lambda _: _OK_RESPONSE)
    assert normalize_raw("NM_003002.2:c.274G>T") == _OK_RESPONSE


def test_normalize_trims_to_pipeline_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mutalyzer_client, "_mt_normalize", lambda _: _OK_RESPONSE)
    result = normalize("NM_003002.2:c.274G>T")
    assert result == {
        "normalized_description": "NM_003002.2:c.274G>T",
        "protein": {"description": "NM_003002.2(NP_002993.1):p.(Asp92Tyr)"},
        "equivalent_descriptions": [{"c": "NM_003002.2:c.274G>T"}],
    }


def test_normalize_raises_on_top_level_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    error_response = {
        "errors": [{"code": "EREF", "details": "reference sequence not found"}],
    }
    monkeypatch.setattr(mutalyzer_client, "_mt_normalize", lambda _: error_response)
    with pytest.raises(MutalyzerError) as exc:
        normalize("NM_999999.9:c.1A>T")
    assert exc.value.code == "EREF"


def test_normalize_raises_on_custom_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    error_response = {"custom": {"errors": [{"code": "ESYNTAX", "details": "bad input"}]}}
    monkeypatch.setattr(mutalyzer_client, "_mt_normalize", lambda _: error_response)
    with pytest.raises(MutalyzerError) as exc:
        normalize("garbage")
    assert exc.value.code == "ESYNTAX"


def test_frameshift_skips_library_and_canonicalises() -> None:
    # No mocking needed — frameshift path doesn't call the library.
    result = normalize_raw("NM_006749.5:p.(R191fs)")
    assert result == {"normalized_description": "NM_006749.5:p.(Arg191fs)"}


def test_frameshift_three_letter_input_preserved() -> None:
    result = normalize_raw("NM_006749.5:p.Arg191GlyfsTer5")
    assert result == {"normalized_description": "NM_006749.5:p.Arg191fs"}


def test_back_translate_returns_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        mutalyzer_client,
        "_mt_back_translate",
        lambda _: ["NM_003002.4:c.(276C>G)", "NM_003002.4:c.(276C>A)"],
    )
    result = back_translate("NP_002993.1:p.Asp92Glu")
    assert result == ["NM_003002.4:c.(276C>G)", "NM_003002.4:c.(276C>A)"]


def test_back_translate_frameshift_rejected() -> None:
    with pytest.raises(MutalyzerError) as exc:
        back_translate("NP_002993.1:p.Arg100GlyfsTer5")
    assert exc.value.code == "FRAMESHIFT_UNSUPPORTED"
