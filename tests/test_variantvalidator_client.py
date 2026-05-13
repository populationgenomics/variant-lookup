"""Tests for the VariantValidator HTTP client (respx-mocked)."""

import httpx
import pytest
import respx

from variant_lookup.variantvalidator_client import (
    VariantValidatorClient,
    VariantValidatorError,
)

_BASE = "http://vv.test"


@pytest.fixture
def client() -> VariantValidatorClient:
    return VariantValidatorClient(_BASE)


@respx.mock
def test_mane_select_happy_path(client: VariantValidatorClient) -> None:
    respx.get(
        f"{_BASE}/VariantValidator/variantvalidator/GRCh38/NM_000551.3%3Ac.1582G%3EA/mane_select"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "NM_000551.3:c.1582G>A": {
                    "primary_assembly_loci": {
                        "grch38": {"vcf": {"chr": "3", "pos": "10141855", "ref": "G", "alt": "A"}}
                    },
                    "hgvs_transcript_variant": "NM_000551.3:c.1582G>A",
                    "hgvs_predicted_protein_consequence": {
                        "tlr": "NP_000542.1:p.(Gly528Asp)",
                    },
                }
            },
        )
    )
    result = client.mane_select("NM_000551.3:c.1582G>A")
    assert result.pseudo_vcf == "3-10141855-G-A"
    assert result.hgvs_c == "NM_000551.3:c.1582G>A"
    assert result.hgvs_p == "NP_000542.1:p.Gly528Asp"  # parens stripped


@respx.mock
def test_mane_select_keyed_by_transcript_falls_back(client: VariantValidatorClient) -> None:
    """VV may key the response by the resolved transcript rather than the input."""
    respx.get(
        f"{_BASE}/VariantValidator/variantvalidator/GRCh38/chr3%3Ag.10141855G%3EA/mane_select"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "NM_000551.3:c.1582G>A": {
                    "primary_assembly_loci": {
                        "grch38": {"vcf": {"chr": "3", "pos": "10141855", "ref": "G", "alt": "A"}}
                    },
                    "hgvs_transcript_variant": "NM_000551.3:c.1582G>A",
                    "hgvs_predicted_protein_consequence": {"tlr": "NP_000542.1:p.Gly528Asp"},
                }
            },
        )
    )
    result = client.mane_select("chr3:g.10141855G>A")
    assert result.pseudo_vcf == "3-10141855-G-A"


@respx.mock
def test_mane_select_no_genomic_coords_raises(client: VariantValidatorClient) -> None:
    respx.get(
        f"{_BASE}/VariantValidator/variantvalidator/GRCh38/NM_xxx%3Ac.1A%3ET/mane_select"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "NM_xxx:c.1A>T": {
                    "primary_assembly_loci": {},
                    "hgvs_transcript_variant": "NM_xxx:c.1A>T",
                    "hgvs_predicted_protein_consequence": {"tlr": "NP_xxx:p.?"},
                }
            },
        )
    )
    with pytest.raises(VariantValidatorError) as exc:
        client.mane_select("NM_xxx:c.1A>T")
    assert exc.value.code == "NO_GENOMIC_COORDS"


@respx.mock
def test_mane_select_upstream_error_raises(client: VariantValidatorClient) -> None:
    respx.get(f"{_BASE}/VariantValidator/variantvalidator/GRCh38/whatever/mane_select").mock(
        return_value=httpx.Response(503, text="overloaded")
    )
    with pytest.raises(VariantValidatorError) as exc:
        client.mane_select("whatever")
    assert exc.value.code == "UPSTREAM_ERROR"
