"""Tests for the NCBI E-utils rsID resolver (respx-mocked)."""

import httpx
import pytest
import respx

from variant_lookup.ncbi import NCBIError, resolve_rsid

_NS = 'xmlns="https://www.ncbi.nlm.nih.gov/SNP/docsum"'


def _docsum_xml(uid: str, hgvs_csv: str, gene: str | None = None) -> str:
    docsum_inner = f"HGVS={hgvs_csv}"
    if gene:
        docsum_inner += f"|GENE={gene}"
    return f"""<?xml version="1.0"?>
<eSummaryResult {_NS}>
  <DocumentSummary uid="{uid}">
    <DOCSUM>{docsum_inner}</DOCSUM>
  </DocumentSummary>
</eSummaryResult>"""


@respx.mock
def test_resolve_returns_hgvs_strings() -> None:
    respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi").mock(
        return_value=httpx.Response(
            200,
            text=_docsum_xml(
                "12345",
                "NM_001257180.2:c.1240G>T,NP_001244109.1:p.Glu414Ter,NC_000008.11:g.42437272C>A",
                gene="SLC20A2:GeneID",
            ),
        )
    )
    result = resolve_rsid("rs12345", email="test@example.com")
    assert result.hgvs_c == "NM_001257180.2:c.1240G>T"
    assert result.hgvs_p == "NP_001244109.1:p.Glu414Ter"
    assert result.hgvs_g == "NC_000008.11:g.42437272C>A"
    assert result.gene == "SLC20A2"


@respx.mock
def test_resolve_unknown_rsid_returns_empty_fields() -> None:
    respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi").mock(
        return_value=httpx.Response(
            200,
            text=f'<?xml version="1.0"?><eSummaryResult {_NS}/>',
        )
    )
    result = resolve_rsid("rs99999999", email="test@example.com")
    assert result.hgvs_c is None
    assert result.hgvs_p is None
    assert result.hgvs_g is None
    assert result.gene is None


def test_invalid_rsid_raises() -> None:
    with pytest.raises(NCBIError) as exc:
        resolve_rsid("not-an-rsid", email="test@example.com")
    assert exc.value.code == "INVALID_RSID"


@respx.mock
def test_upstream_error_raises() -> None:
    respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi").mock(
        return_value=httpx.Response(503, text="service unavailable")
    )
    with pytest.raises(NCBIError) as exc:
        resolve_rsid("rs12345", email="test@example.com")
    assert exc.value.code == "UPSTREAM_ERROR"


@respx.mock
def test_resolve_passes_api_key_when_set() -> None:
    route = respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi").mock(
        return_value=httpx.Response(
            200,
            text=f'<?xml version="1.0"?><eSummaryResult {_NS}/>',
        )
    )
    resolve_rsid("rs42", email="test@example.com", api_key="secret-key")
    assert route.called
    request = route.calls[0].request
    assert request.url.params["api_key"] == "secret-key"
    assert request.url.params["tool"] == "variant-lookup"
