"""Tests for the HGVS cleanup pipeline (no external services)."""

import pytest

from variant_lookup.normalize import (
    CleanedVariant,
    VariantCleanupError,
    clean,
    extract_rsid,
)
from variant_lookup.refseq import GeneAccessions, RefSeqIndex


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


# --- rsID detection -------------------------------------------------------


class TestExtractRsid:
    def test_bare_rsid(self) -> None:
        assert extract_rsid("rs12345") == "rs12345"

    def test_embedded_rsid(self) -> None:
        assert extract_rsid("MT-TL1:rs199474657") == "rs199474657"

    def test_no_rsid_returns_none(self) -> None:
        assert extract_rsid("c.1240G>T") is None

    def test_rsid_in_gibberish_returns_match(self) -> None:
        assert extract_rsid("foo_rs42_bar") == "rs42"


# --- HGVS cleanup ---------------------------------------------------------


class TestCleanHGVS:
    def test_plain_coding_with_gene(self, refseq_index: RefSeqIndex) -> None:
        result = clean("c.1240G>T", "SLC20A2", "GRCh38", refseq_index)
        assert result == CleanedVariant(refseq="NM_006749.5", hgvs_desc="c.1240G>T")

    def test_plain_protein_with_gene(self, refseq_index: RefSeqIndex) -> None:
        result = clean("p.Glu414Ter", "SLC20A2", "GRCh38", refseq_index)
        # protein refseq predicted as transcript(protein)
        assert result.refseq == "NM_006749.5(NP_006740.1)"
        assert result.hgvs_desc == "p.Glu414Ter"

    def test_gene_prefix_stripped(self, refseq_index: RefSeqIndex) -> None:
        result = clean("SLC20A2:c.1240G>T", "SLC20A2", "GRCh38", refseq_index)
        assert result.hgvs_desc == "c.1240G>T"

    def test_gene_parens_stripped(self, refseq_index: RefSeqIndex) -> None:
        result = clean("SLC20A2(c.1240G>T)", "SLC20A2", "GRCh38", refseq_index)
        assert result.hgvs_desc == "c.1240G>T"

    def test_whitespace_stripped(self, refseq_index: RefSeqIndex) -> None:
        result = clean("c.1240 G>T", "SLC20A2", "GRCh38", refseq_index)
        assert result.hgvs_desc == "c.1240G>T"

    def test_parens_around_protein_stripped(self, refseq_index: RefSeqIndex) -> None:
        result = clean("p.(Glu414Ter)", "SLC20A2", "GRCh38", refseq_index)
        assert result.hgvs_desc == "p.Glu414Ter"

    def test_x_normalised_to_stop_codon(self, refseq_index: RefSeqIndex) -> None:
        """``X`` → ``*`` then ``R`` → ``Arg`` (mutalyzer 3 demands 3-letter)."""
        result = clean("p.R191X", "PDGFB", "GRCh38", refseq_index)
        assert result.hgvs_desc == "p.Arg191*"

    def test_single_letter_aa_expanded(self, refseq_index: RefSeqIndex) -> None:
        """Bare 1-letter substitution: ``p.R256H`` → ``p.Arg256His``."""
        result = clean("p.R256H", "PDGFB", "GRCh38", refseq_index)
        assert result.hgvs_desc == "p.Arg256His"

    def test_single_letter_aa_in_range(self, refseq_index: RefSeqIndex) -> None:
        """Range ``p.K59_N98del`` → ``p.Lys59_Asn98del``."""
        result = clean("p.K59_N98del", "PDGFB", "GRCh38", refseq_index)
        assert result.hgvs_desc == "p.Lys59_Asn98del"

    def test_three_letter_aa_left_alone(self, refseq_index: RefSeqIndex) -> None:
        """Already-3-letter codes (lowercase 2nd char) are not touched."""
        result = clean("p.Arg63Cys", "PDGFB", "GRCh38", refseq_index)
        assert result.hgvs_desc == "p.Arg63Cys"

    def test_unknown_aa_letter_passes_through(self, refseq_index: RefSeqIndex) -> None:
        """U (selenocysteine), B and similar non-standard letters are preserved."""
        result = clean("p.U191Q", "PDGFB", "GRCh38", refseq_index)
        # U has no 3-letter mapping in our table; Q expands.
        assert result.hgvs_desc == "p.U191Gln"

    def test_three_letter_capitalization(self, refseq_index: RefSeqIndex) -> None:
        result = clean("p.arg191ter", "PDGFB", "GRCh38", refseq_index)
        assert result.hgvs_desc == "p.Arg191Ter"

    def test_explicit_refseq_passthrough(self, refseq_index: RefSeqIndex) -> None:
        result = clean("NM_001257180.2:c.1240G>T", "SLC20A2", "GRCh38", refseq_index)
        assert result.refseq == "NM_001257180.2"

    def test_versionless_refseq_autocomplete(self, refseq_index: RefSeqIndex) -> None:
        result = clean("NM_006749:c.1240G>T", "SLC20A2", "GRCh38", refseq_index)
        assert result.refseq == "NM_006749.5"

    def test_chromosomal_resolves_to_nc_accession(self, refseq_index: RefSeqIndex) -> None:
        result = clean("chr8:g.42437272C>A", "SLC20A2", "GRCh38", refseq_index)
        assert result.refseq == "NC_000008.11"
        assert result.hgvs_desc == "g.42437272C>A"

    def test_chromosomal_grch37(self, refseq_index: RefSeqIndex) -> None:
        result = clean("chr8:g.42437272C>A", "SLC20A2", "GRCh37", refseq_index)
        assert result.refseq == "NC_000008.10"

    def test_chr_x_genomic(self, refseq_index: RefSeqIndex) -> None:
        result = clean("chrX:g.100A>G", None, "GRCh38", refseq_index)
        assert result.refseq == "NC_000023.11"

    def test_mitochondrial(self, refseq_index: RefSeqIndex) -> None:
        result = clean("m.3243A>G", None, "GRCh38", refseq_index)
        assert result.refseq == "NC_012920.1"
        assert result.hgvs_desc == "m.3243A>G"

    def test_frameshift_normalisation(self, refseq_index: RefSeqIndex) -> None:
        result = clean("p.Arg100GlyfsTer5", "SLC20A2", "GRCh38", refseq_index)
        assert result.hgvs_desc.endswith("fs")

    def test_intronic_splice_wrapped_with_genomic_ref(self, refseq_index: RefSeqIndex) -> None:
        """``c.X+N`` / ``c.X-N`` need the chromosomal NC_ for Mutalyzer to resolve.

        Per HGVS nomenclature, intronic positions in transcript coordinates
        must be qualified with the genomic reference: ``NC_chr(NM_X):c.X+N``.
        """
        result = clean("c.1240+1G>T", "SLC20A2", "GRCh38", refseq_index)
        assert result.refseq == "NC_000008.11(NM_006749.5)"
        assert result.hgvs_desc == "c.1240+1G>T"

    def test_intronic_wrapping_on_caller_supplied_nm(self, refseq_index: RefSeqIndex) -> None:
        """Caller-supplied bare ``NM_:c.X-N`` is wrapped the same way."""
        result = clean("NM_006749.5:c.1240-2A>G", None, "GRCh38", refseq_index)
        assert result.refseq == "NC_000008.11(NM_006749.5)"
        assert result.hgvs_desc == "c.1240-2A>G"

    def test_non_intronic_not_wrapped(self, refseq_index: RefSeqIndex) -> None:
        """A plain coding-change c.X>Y stays as a bare NM_."""
        result = clean("NM_006749.5:c.1240G>T", None, "GRCh38", refseq_index)
        assert result.refseq == "NM_006749.5"

    def test_intronic_unknown_transcript_passes_through(self, refseq_index: RefSeqIndex) -> None:
        """If the NM_ isn't in our index we can't wrap — leave it to upstream."""
        result = clean("NM_999999.1:c.100+1G>T", None, "GRCh38", refseq_index)
        assert result.refseq == "NM_999999.1"


class TestCleanRejects:
    def test_rsid_rejected(self, refseq_index: RefSeqIndex) -> None:
        with pytest.raises(VariantCleanupError, match="rsID"):
            clean("rs12345", "SLC20A2", "GRCh38", refseq_index)

    def test_chromosomal_without_build_rejected(self, refseq_index: RefSeqIndex) -> None:
        with pytest.raises(VariantCleanupError, match="genome build"):
            clean("chr8:g.42437272C>A", "SLC20A2", None, refseq_index)

    def test_unknown_build_rejected(self, refseq_index: RefSeqIndex) -> None:
        with pytest.raises(VariantCleanupError, match="unknown genome build"):
            clean("chr8:g.42437272C>A", "SLC20A2", "GRCh99", refseq_index)

    def test_coding_without_gene_or_refseq_rejected(self, refseq_index: RefSeqIndex) -> None:
        with pytest.raises(VariantCleanupError, match=r"RefSeq prefix.*gene symbol"):
            clean("c.1240G>T", None, "GRCh38", refseq_index)

    def test_coding_with_unknown_gene_rejected_clearly(self, refseq_index: RefSeqIndex) -> None:
        """Gene provided but absent from the RefSeq index — clear, gene-specific message."""
        with pytest.raises(
            VariantCleanupError, match="no MANE-Select transcript known for gene 'UNKNOWN'"
        ):
            clean("c.1240G>T", "UNKNOWN", "GRCh38", refseq_index)

    def test_unparseable_input_rejected(self, refseq_index: RefSeqIndex) -> None:
        with pytest.raises(VariantCleanupError, match="unparsable"):
            clean("12345", "SLC20A2", "GRCh38", refseq_index)
