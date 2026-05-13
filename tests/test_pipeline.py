"""Tests for the per-variant pipeline orchestrator (mocked dependencies)."""

from unittest.mock import MagicMock

import pytest

from variant_lookup import echtvar, mutalyzer_client, ncbi, pipeline
from variant_lookup.config import Settings, get_settings
from variant_lookup.models import Frequency, VariantInput
from variant_lookup.ncbi import RsIDResolution
from variant_lookup.refseq import GeneAccessions, RefSeqIndex
from variant_lookup.variantvalidator_client import (
    VariantValidatorClient,
    VariantValidatorError,
    VVResult,
)


@pytest.fixture
def refseq_index() -> RefSeqIndex:
    return RefSeqIndex(
        {
            "SLC20A2": GeneAccessions(
                symbol="SLC20A2",
                rna="NM_006749.5",
                protein="NP_006740.1",
                genomic="NC_000008.11",
                gene_id="6575",
                mane=True,
            ),
            "PDGFB": GeneAccessions(
                symbol="PDGFB",
                rna="NM_002608.4",
                protein="NP_002599.1",
                genomic="NC_000022.11",
                gene_id="5155",
                mane=True,
            ),
        }
    )


@pytest.fixture
def settings() -> Settings:
    return get_settings()


@pytest.fixture
def vv_client() -> MagicMock:
    return MagicMock(spec=VariantValidatorClient)


@pytest.fixture
def pipe(settings: Settings, refseq_index: RefSeqIndex, vv_client: MagicMock) -> pipeline.Pipeline:
    return pipeline.Pipeline(settings=settings, refseq_index=refseq_index, vv_client=vv_client)


def _fake_freq() -> Frequency:
    return Frequency(
        ac=5,
        an=1614174,
        homozygote_count=0,
        hemizygote_count=0,
        faf95_popmax=None,
        faf95_popmax_population=None,
    )


# ----- happy paths --------------------------------------------------------


def test_coding_variant_resolves_to_one_normalized(
    pipe: pipeline.Pipeline,
    vv_client: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        mutalyzer_client,
        "normalize",
        lambda _: {"normalized_description": "NM_006749.5:c.1240G>T"},
    )
    vv_client.mane_select.return_value = VVResult(
        pseudo_vcf="8-42437272-C-A",
        hgvs_c="NM_006749.5:c.1240G>T",
        hgvs_p="NP_006740.1:p.Glu414Ter",
    )
    freq = _fake_freq()
    monkeypatch.setattr(echtvar, "annotate", lambda _, **__: [freq])

    response = pipe.process_batch(
        [VariantInput(id="v1", gene="SLC20A2", hgnc_id=11013, variant="c.1240G>T")],
        "GRCh38",
    )

    assert len(response.results) == 1
    result = response.results[0]
    assert result.error is None
    assert result.normalized is not None
    assert len(result.normalized) == 1
    assert result.normalized[0].pseudo_vcf == "8-42437272-C-A"
    assert result.normalized[0].frequency == freq


def test_protein_variant_fans_out_to_multiple_candidates(
    pipe: pipeline.Pipeline,
    vv_client: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        mutalyzer_client,
        "normalize",
        lambda _: {"normalized_description": "NM_006749.5(NP_006740.1):p.Glu414Ter"},
    )
    monkeypatch.setattr(
        mutalyzer_client,
        "back_translate",
        lambda _: ["NM_006749.5:c.1240G>T", "NM_006749.5:c.1240G>A"],
    )
    vv_client.mane_select.side_effect = [
        VVResult(
            pseudo_vcf="8-42437272-C-A",
            hgvs_c="NM_006749.5:c.1240G>T",
            hgvs_p="NP_006740.1:p.Glu414Ter",
        ),
        VVResult(
            pseudo_vcf="8-42437272-C-T",
            hgvs_c="NM_006749.5:c.1240G>A",
            hgvs_p="NP_006740.1:p.Glu414Lys",
        ),
    ]
    monkeypatch.setattr(echtvar, "annotate", lambda _, **__: [None, None])

    response = pipe.process_batch(
        [VariantInput(id="v1", gene="SLC20A2", hgnc_id=11013, variant="p.Glu414Ter")],
        "GRCh38",
    )

    result = response.results[0]
    assert result.error is None
    assert result.normalized is not None
    assert len(result.normalized) == 2
    assert {n.pseudo_vcf for n in result.normalized} == {"8-42437272-C-A", "8-42437272-C-T"}


def test_rsid_input_routes_to_ncbi(
    pipe: pipeline.Pipeline,
    vv_client: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ncbi,
        "resolve_rsid",
        lambda *_, **__: RsIDResolution(
            hgvs_c="NM_006749.5:c.1240G>T",
            hgvs_p=None,
            hgvs_g=None,
            gene="SLC20A2",
        ),
    )
    monkeypatch.setattr(
        mutalyzer_client,
        "normalize",
        lambda _: {"normalized_description": "NM_006749.5:c.1240G>T"},
    )
    vv_client.mane_select.return_value = VVResult(
        pseudo_vcf="8-42437272-C-A",
        hgvs_c="NM_006749.5:c.1240G>T",
        hgvs_p="NP_006740.1:p.Glu414Ter",
    )
    monkeypatch.setattr(echtvar, "annotate", lambda _, **__: [None])

    response = pipe.process_batch(
        [VariantInput(id="v1", gene="SLC20A2", hgnc_id=11013, variant="rs12345")],
        "GRCh38",
    )

    result = response.results[0]
    assert result.error is None
    assert result.normalized is not None
    assert result.normalized[0].pseudo_vcf == "8-42437272-C-A"


# ----- error paths --------------------------------------------------------


def test_cleanup_failure_produces_error_result(pipe: pipeline.Pipeline) -> None:
    # Unparseable input — no letters means cleanup rejects it
    response = pipe.process_batch(
        [VariantInput(id="v1", gene="SLC20A2", hgnc_id=11013, variant="12345")],
        "GRCh38",
    )
    result = response.results[0]
    assert result.error is not None
    assert result.error.code == "VARIANT_CLEANUP_FAILED"
    assert result.normalized is None


def test_vv_failure_produces_error_result(
    pipe: pipeline.Pipeline,
    vv_client: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        mutalyzer_client,
        "normalize",
        lambda _: {"normalized_description": "NM_006749.5:c.1240G>T"},
    )
    vv_client.mane_select.side_effect = VariantValidatorError("NO_GENOMIC_COORDS", "nope")

    response = pipe.process_batch(
        [VariantInput(id="v1", gene="SLC20A2", hgnc_id=11013, variant="c.1240G>T")],
        "GRCh38",
    )
    result = response.results[0]
    assert result.error is not None
    assert result.error.code == "NO_GENOMIC_COORDS"
    assert result.error.upstream == "variantvalidator"


def test_mutalyzer_normalize_failure_produces_error_result(
    pipe: pipeline.Pipeline,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_(_: str) -> dict[str, object]:
        raise mutalyzer_client.MutalyzerError("EREF", "reference not found")

    monkeypatch.setattr(mutalyzer_client, "normalize", raise_)

    response = pipe.process_batch(
        [VariantInput(id="v1", gene="SLC20A2", hgnc_id=11013, variant="c.1240G>T")],
        "GRCh38",
    )
    result = response.results[0]
    assert result.error is not None
    assert result.error.code == "NORMALIZATION_EREF"
    assert result.error.upstream == "mutalyzer"


def test_partial_batch_mixes_success_and_error(
    pipe: pipeline.Pipeline,
    vv_client: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        mutalyzer_client,
        "normalize",
        lambda _: {"normalized_description": "NM_006749.5:c.1240G>T"},
    )
    vv_client.mane_select.return_value = VVResult(
        pseudo_vcf="8-42437272-C-A",
        hgvs_c="NM_006749.5:c.1240G>T",
        hgvs_p="NP_006740.1:p.Glu414Ter",
    )
    monkeypatch.setattr(echtvar, "annotate", lambda _, **__: [None])

    response = pipe.process_batch(
        [
            VariantInput(id="g1", gene="SLC20A2", hgnc_id=11013, variant="c.1240G>T"),
            VariantInput(id="b1", gene="SLC20A2", hgnc_id=11013, variant="12345"),
        ],
        "GRCh38",
    )

    assert response.results[0].error is None
    assert response.results[0].normalized is not None
    assert response.results[1].error is not None
    assert response.results[1].error.code == "VARIANT_CLEANUP_FAILED"


def test_meta_block_populated(
    pipe: pipeline.Pipeline,
) -> None:
    response = pipe.process_batch([], "GRCh38")
    assert response.meta.reference == "GRCh38"
    assert response.meta.gnomad
    assert response.meta.timestamp
