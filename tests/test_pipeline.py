"""Tests for the per-variant pipeline orchestrator (mocked dependencies)."""

from unittest.mock import MagicMock

import pytest

from variant_lookup import echtvar, ncbi, pipeline
from variant_lookup.config import Settings, get_settings
from variant_lookup.models import Frequency, VariantInput
from variant_lookup.mutalyzer_client import MutalyzerClient, MutalyzerError
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
def mut_client() -> MagicMock:
    return MagicMock(spec=MutalyzerClient)


@pytest.fixture
def pipe(
    settings: Settings,
    refseq_index: RefSeqIndex,
    vv_client: MagicMock,
    mut_client: MagicMock,
) -> pipeline.Pipeline:
    return pipeline.Pipeline(
        settings=settings,
        refseq_index=refseq_index,
        vv_client=vv_client,
        mutalyzer_client=mut_client,
    )


def _fake_freq() -> Frequency:
    return Frequency(
        ac=5,
        an=1614174,
        homozygote_count=0,
        heterozygote_count=5,
        hemizygote_count=0,
        faf95_popmax=None,
        faf95_popmax_population=None,
    )


# ----- happy paths --------------------------------------------------------


def test_coding_variant_resolves_to_one_normalized(
    pipe: pipeline.Pipeline,
    vv_client: MagicMock,
    mut_client: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mut_client.normalize.return_value = {"normalized_description": "NM_006749.5:c.1240G>T"}
    vv_client.mane_select.return_value = VVResult(
        pseudo_vcf="8-42437272-C-A",
        hgvs_c="NM_006749.5:c.1240G>T",
        hgvs_p="NP_006740.1:p.Glu414Ter",
    )
    freq = _fake_freq()
    monkeypatch.setattr(echtvar, "annotate", lambda _, **__: [freq])

    result = pipe.process_one(VariantInput(gene="SLC20A2", variant="c.1240G>T"), "GRCh38")

    assert result.error is None
    assert result.normalized is not None
    assert len(result.normalized) == 1
    assert result.normalized[0].pseudo_vcf == "8-42437272-C-A"
    assert result.normalized[0].frequency == freq


def test_protein_variant_fans_out_to_multiple_candidates(
    pipe: pipeline.Pipeline,
    vv_client: MagicMock,
    mut_client: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mut_client.normalize.return_value = {
        "normalized_description": "NM_006749.5(NP_006740.1):p.Glu414Ter"
    }
    mut_client.back_translate.return_value = [
        "NM_006749.5:c.1240G>T",
        "NM_006749.5:c.1240G>A",
    ]
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

    result = pipe.process_one(VariantInput(gene="SLC20A2", variant="p.Glu414Ter"), "GRCh38")

    assert result.error is None
    assert result.normalized is not None
    assert len(result.normalized) == 2
    assert {n.pseudo_vcf for n in result.normalized} == {"8-42437272-C-A", "8-42437272-C-T"}


def test_rsid_input_routes_to_ncbi(
    pipe: pipeline.Pipeline,
    vv_client: MagicMock,
    mut_client: MagicMock,
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
    mut_client.normalize.return_value = {"normalized_description": "NM_006749.5:c.1240G>T"}
    vv_client.mane_select.return_value = VVResult(
        pseudo_vcf="8-42437272-C-A",
        hgvs_c="NM_006749.5:c.1240G>T",
        hgvs_p="NP_006740.1:p.Glu414Ter",
    )
    monkeypatch.setattr(echtvar, "annotate", lambda _, **__: [None])

    result = pipe.process_one(VariantInput(gene="SLC20A2", variant="rs12345"), "GRCh38")

    assert result.error is None
    assert result.normalized is not None
    assert result.normalized[0].pseudo_vcf == "8-42437272-C-A"


# ----- error paths --------------------------------------------------------


def test_cleanup_failure_produces_error_result(pipe: pipeline.Pipeline) -> None:
    # Unparseable input — no letters means cleanup rejects it
    result = pipe.process_one(VariantInput(gene="SLC20A2", variant="12345"), "GRCh38")
    assert result.error is not None
    assert result.error.code == "VARIANT_CLEANUP_FAILED"
    assert result.normalized is None


def test_vv_failure_produces_error_result(
    pipe: pipeline.Pipeline,
    vv_client: MagicMock,
    mut_client: MagicMock,
) -> None:
    mut_client.normalize.return_value = {"normalized_description": "NM_006749.5:c.1240G>T"}
    vv_client.mane_select.side_effect = VariantValidatorError("NO_GENOMIC_COORDS", "nope")

    result = pipe.process_one(VariantInput(gene="SLC20A2", variant="c.1240G>T"), "GRCh38")
    assert result.error is not None
    assert result.error.code == "NO_GENOMIC_COORDS"
    assert result.error.upstream == "variantvalidator"


def test_vv_timeout_surfaces_as_retriable_code(
    pipe: pipeline.Pipeline,
    vv_client: MagicMock,
    mut_client: MagicMock,
) -> None:
    """VV cold-cache timeouts get VV_UPSTREAM_TIMEOUT (retriable), not NO_GENOMIC_COORDS."""
    mut_client.normalize.return_value = {"normalized_description": "NM_006749.5:c.1240G>T"}
    vv_client.mane_select.side_effect = VariantValidatorError("UPSTREAM_TIMEOUT", "read timed out")

    result = pipe.process_one(VariantInput(gene="SLC20A2", variant="c.1240G>T"), "GRCh38")
    assert result.error is not None
    assert result.error.code == "VV_UPSTREAM_TIMEOUT"
    assert result.error.upstream == "variantvalidator"


def test_vv_timeout_with_other_candidates_failing_still_retriable(
    pipe: pipeline.Pipeline,
    vv_client: MagicMock,
    mut_client: MagicMock,
) -> None:
    """Protein input back-translates to multiple candidates; if any timed out
    (and none resolved), the request is retriable — surfaced as VV_UPSTREAM_TIMEOUT."""
    mut_client.normalize.return_value = {"normalized_description": "NM_X(NP_Y):p.Arg191Gln"}
    mut_client.back_translate.return_value = ["NM_X:c.572G>A", "NM_X:c.571C>T"]
    vv_client.mane_select.side_effect = [
        VariantValidatorError("UPSTREAM_TIMEOUT", "read timed out"),
        VariantValidatorError("NO_GENOMIC_COORDS", "VV had nothing"),
    ]

    result = pipe.process_one(VariantInput(gene="PDGFB", variant="p.Arg191Gln"), "GRCh38")
    assert result.error is not None
    assert result.error.code == "VV_UPSTREAM_TIMEOUT"


def test_mutalyzer_normalize_timeout_surfaces_as_retriable_code(
    pipe: pipeline.Pipeline,
    mut_client: MagicMock,
) -> None:
    mut_client.normalize.side_effect = MutalyzerError("UPSTREAM_TIMEOUT", "read timed out")

    result = pipe.process_one(VariantInput(gene="SLC20A2", variant="c.1240G>T"), "GRCh38")
    assert result.error is not None
    assert result.error.code == "NORMALIZATION_UPSTREAM_TIMEOUT"
    assert result.error.upstream == "mutalyzer"


def test_mutalyzer_normalize_failure_produces_error_result(
    pipe: pipeline.Pipeline,
    mut_client: MagicMock,
) -> None:
    mut_client.normalize.side_effect = MutalyzerError("EREF", "reference not found")

    result = pipe.process_one(VariantInput(gene="SLC20A2", variant="c.1240G>T"), "GRCh38")
    assert result.error is not None
    assert result.error.code == "NORMALIZATION_EREF"
    assert result.error.upstream == "mutalyzer"


# ----- meta + durations --------------------------------------------------


def test_meta_block_populated_on_error(pipe: pipeline.Pipeline) -> None:
    """Even on an early-cleanup-failure path, meta + durations_ms are emitted."""
    result = pipe.process_one(VariantInput(gene="SLC20A2", variant="12345"), "GRCh38")
    assert result.error is not None
    assert result.meta.reference == "GRCh38"
    assert result.meta.gnomad
    assert result.meta.timestamp
    assert set(result.meta.durations_ms.keys()) == {
        "cleanup",
        "rsid",
        "normalize",
        "back_translate",
        "variantvalidator",
        "echtvar",
        "total",
    }
    assert all(isinstance(v, int) and v >= 0 for v in result.meta.durations_ms.values())


def test_meta_durations_total_covers_per_stage(
    pipe: pipeline.Pipeline,
    vv_client: MagicMock,
    mut_client: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """total should be >= sum of per-stage durations (modulo rounding)."""
    mut_client.normalize.return_value = {"normalized_description": "NM_006749.5:c.1240G>T"}
    vv_client.mane_select.return_value = VVResult(
        pseudo_vcf="8-42437272-C-A",
        hgvs_c="NM_006749.5:c.1240G>T",
        hgvs_p="NP_006740.1:p.Glu414Ter",
    )
    monkeypatch.setattr(echtvar, "annotate", lambda _, **__: [_fake_freq()])

    result = pipe.process_one(VariantInput(gene="SLC20A2", variant="c.1240G>T"), "GRCh38")
    d = result.meta.durations_ms
    stage_sum = sum(
        d[k]
        for k in ("cleanup", "rsid", "normalize", "back_translate", "variantvalidator", "echtvar")
    )
    # Allow some ms slop for rounding (each stage is rounded independently).
    assert d["total"] + 6 >= stage_sum


# ----- gene-optional schema ----------------------------------------------


def test_fully_qualified_input_works_without_gene(
    pipe: pipeline.Pipeline,
    vv_client: MagicMock,
    mut_client: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A NC_…:g.… input has all info inline — `gene` is optional."""
    mut_client.normalize.return_value = {"normalized_description": "NC_000016.10:g.2116896C>A"}
    vv_client.mane_select.return_value = VVResult(
        pseudo_vcf="16-2116896-C-A",
        hgvs_c="NM_001009944.3:c.1543G>T",
        hgvs_p="NP_001009944.3:p.Gly515Trp",
    )
    monkeypatch.setattr(echtvar, "annotate", lambda _, **__: [None])

    result = pipe.process_one(
        VariantInput(gene=None, variant="NC_000016.10:g.2116896C>A"), "GRCh38"
    )
    assert result.error is None
    assert result.normalized is not None
    assert result.normalized[0].pseudo_vcf == "16-2116896-C-A"


def test_bare_variant_without_gene_fails_with_clear_message(
    pipe: pipeline.Pipeline,
) -> None:
    """Bare c.… with no gene and no RefSeq prefix must produce a clear error."""
    result = pipe.process_one(VariantInput(gene=None, variant="c.1240G>T"), "GRCh38")
    assert result.error is not None
    assert result.error.code == "VARIANT_CLEANUP_FAILED"
    # Message should mention what's missing, not include the literal "None".
    assert "None" not in result.error.message
    assert "gene symbol" in result.error.message or "RefSeq prefix" in result.error.message
