"""End-to-end tests for POST /v1/variant with mocked upstreams."""

from unittest.mock import patch

from fastapi.testclient import TestClient

from tests.conftest import TEST_BEARER
from variant_lookup.api import create_app
from variant_lookup.models import Frequency
from variant_lookup.variantvalidator_client import VVResult


def test_e2e_coding_variant_returns_normalized_with_frequency() -> None:
    fake_freq = Frequency(
        ac=5,
        an=1614174,
        homozygote_count=0,
        heterozygote_count=5,
        hemizygote_count=0,
        faf95_popmax=None,
        faf95_popmax_population=None,
    )
    fake_vv = VVResult(
        pseudo_vcf="8-42437272-C-A",
        hgvs_c="NM_006749.5:c.1240G>T",
        hgvs_p="NP_006740.1:p.Glu414Ter",
    )

    with (
        patch("variant_lookup.api.VariantValidatorClient") as VVC,
        patch("variant_lookup.api.MutalyzerClient") as MC,
        patch("variant_lookup.pipeline.echtvar.annotate", return_value=[fake_freq]),
    ):
        VVC.return_value.mane_select.return_value = fake_vv
        MC.return_value.normalize.return_value = {"normalized_description": "NM_006749.5:c.1240G>T"}
        client = TestClient(create_app())
        response = client.post(
            "/v1/variant",
            json={
                "genome_build": "GRCh38",
                "id": "v1",
                "gene": "SLC20A2",
                "variant": "NM_006749.5:c.1240G>T",
            },
            headers={"Authorization": f"Bearer {TEST_BEARER}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "v1"
    assert body["error"] is None
    assert body["normalized"][0]["pseudo_vcf"] == "8-42437272-C-A"
    assert body["normalized"][0]["frequency"]["ac"] == 5
    # meta + durations still emitted in single-variant shape
    assert body["meta"]["reference"] == "GRCh38"
    assert "durations_ms" in body["meta"]
