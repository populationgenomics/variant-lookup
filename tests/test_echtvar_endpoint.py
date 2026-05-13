"""Tests for the /echtvar/frequencies passthrough endpoint."""

from unittest.mock import patch

from fastapi.testclient import TestClient

from tests.conftest import TEST_BEARER
from variant_lookup.api import create_app
from variant_lookup.models import Frequency


def test_requires_auth() -> None:
    client = TestClient(create_app())
    response = client.post("/echtvar/frequencies", json={"variants": ["1-100-A-G"]})
    assert response.status_code == 401


def test_returns_frequencies_in_request_order() -> None:
    fake_freqs: list[Frequency | None] = [
        Frequency(
            ac=5,
            an=100,
            homozygote_count=0,
            heterozygote_count=5,
            hemizygote_count=0,
            faf95_popmax=None,
            faf95_popmax_population=None,
        ),
        None,
    ]
    with patch("variant_lookup.api.echtvar.annotate", return_value=fake_freqs):
        client = TestClient(create_app())
        response = client.post(
            "/echtvar/frequencies",
            json={"variants": ["1-100-A-G", "2-200-C-T"]},
            headers={"Authorization": f"Bearer {TEST_BEARER}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["meta"]["gnomad"]
    assert body["meta"]["reference"] == "GRCh38"
    assert len(body["results"]) == 2
    assert body["results"][0]["pseudo_vcf"] == "1-100-A-G"
    assert body["results"][0]["frequency"]["ac"] == 5
    assert body["results"][1]["frequency"] is None


def test_rejects_oversized_batch() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/echtvar/frequencies",
        json={"variants": [f"1-{i}-A-G" for i in range(1001)]},
        headers={"Authorization": f"Bearer {TEST_BEARER}"},
    )
    assert response.status_code == 422  # pydantic validation rejects > 1000
