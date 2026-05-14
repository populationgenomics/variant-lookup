"""Tests for the RefSeq GFF parser (refseq_build.process_gff).

Regression test for the two-pass parse: GFF3 emits each gene's mRNA
record before its CDS record, so a single-pass parser would skip
every transcript on a "gene not yet recorded" guard.
"""

import gzip
import textwrap
from pathlib import Path

from variant_lookup.refseq_build import process_gff


def _write_gff(tmp_path: Path, content: str) -> Path:
    target = tmp_path / "refseq.gff.gz"
    with gzip.open(target, "wt") as f:
        f.write(textwrap.dedent(content).lstrip("\n"))
    return target


def test_mrna_before_cds_still_records_transcript(tmp_path: Path) -> None:
    """The standard GFF3 ordering: mRNA precedes CDS for each gene."""
    gff = _write_gff(
        tmp_path,
        """
        NC_000014.9\tBestRefSeq\tgene\t1\t1000\t.\t+\t.\tID=gene-MYH7;gene=MYH7
        NC_000014.9\tBestRefSeq\tmRNA\t1\t1000\t.\t+\t.\tID=rna-NM_000257.4;Parent=gene-MYH7;Dbxref=GeneID:4625;gene=MYH7;tag=MANE Select;transcript_id=NM_000257.4
        NC_000014.9\tBestRefSeq\texon\t1\t500\t.\t+\t.\tID=exon-1;Parent=rna-NM_000257.4
        NC_000014.9\tBestRefSeq\tCDS\t1\t999\t.\t+\t0\tID=cds-NP_000248.2;Parent=rna-NM_000257.4;Dbxref=GeneID:4625;gene=MYH7;protein_id=NP_000248.2;tag=MANE Select
        """,
    )
    refseqs = process_gff(gff)
    assert refseqs == {
        "MYH7": {
            "Protein": "NP_000248.2",
            "Genomic": "NC_000014.9",
            "Symbol": "MYH7",
            "GeneID": "4625",
            "MANE": True,
            "RNA": "NM_000257.4",
        }
    }


def test_lines_without_select_tag_are_skipped(tmp_path: Path) -> None:
    gff = _write_gff(
        tmp_path,
        """
        NC_000001.11\tBestRefSeq\tmRNA\t1\t1000\t.\t+\t.\tID=rna-NM_000001.1;Parent=gene-FOO;gene=FOO;transcript_id=NM_000001.1
        NC_000001.11\tBestRefSeq\tCDS\t1\t999\t.\t+\t0\tID=cds-NP_000001.1;Parent=rna-NM_000001.1;Dbxref=GeneID:1;gene=FOO;protein_id=NP_000001.1
        """,
    )
    assert process_gff(gff) == {}


def test_non_bestrefseq_lines_are_skipped(tmp_path: Path) -> None:
    gff = _write_gff(
        tmp_path,
        """
        NC_000001.11\tGnomon\tmRNA\t1\t1000\t.\t+\t.\tID=rna-XM_X;gene=FOO;tag=MANE Select;transcript_id=XM_X
        NC_000001.11\tCurated Genomic\tCDS\t1\t999\t.\t+\t0\tID=cds-XP_X;Dbxref=GeneID:1;gene=FOO;protein_id=XP_X;tag=MANE Select
        """,
    )
    assert process_gff(gff) == {}


def test_mane_select_preferred_over_refseq_select(tmp_path: Path) -> None:
    """If both a RefSeq Select and a MANE Select CDS exist, MANE wins."""
    gff = _write_gff(
        tmp_path,
        """
        NC_000007.14\tBestRefSeq\tmRNA\t1\t1000\t.\t+\t.\tID=rna-NM_REFSEQ.1;Parent=gene-BRAF;Dbxref=GeneID:673;gene=BRAF;tag=RefSeq Select;transcript_id=NM_REFSEQ.1
        NC_000007.14\tBestRefSeq\tCDS\t1\t999\t.\t+\t0\tID=cds-NP_REFSEQ.1;Parent=rna-NM_REFSEQ.1;Dbxref=GeneID:673;gene=BRAF;protein_id=NP_REFSEQ.1;tag=RefSeq Select
        NC_000007.14\tBestRefSeq\tmRNA\t1\t1000\t.\t+\t.\tID=rna-NM_MANE.1;Parent=gene-BRAF;Dbxref=GeneID:673;gene=BRAF;tag=MANE Select;transcript_id=NM_MANE.1
        NC_000007.14\tBestRefSeq\tCDS\t1\t999\t.\t+\t0\tID=cds-NP_MANE.1;Parent=rna-NM_MANE.1;Dbxref=GeneID:673;gene=BRAF;protein_id=NP_MANE.1;tag=MANE Select
        """,
    )
    refseqs = process_gff(gff)
    assert refseqs["BRAF"]["MANE"] is True
    assert refseqs["BRAF"]["Protein"] == "NP_MANE.1"


def test_transcript_without_protein_is_dropped(tmp_path: Path) -> None:
    """Non-coding transcripts (no CDS) shouldn't create dangling entries."""
    gff = _write_gff(
        tmp_path,
        """
        NC_000001.11\tBestRefSeq\tmRNA\t1\t1000\t.\t+\t.\tID=rna-NR_X.1;Parent=gene-LNCFOO;Dbxref=GeneID:99;gene=LNCFOO;tag=MANE Select;transcript_id=NR_X.1
        """,
    )
    assert process_gff(gff) == {}
