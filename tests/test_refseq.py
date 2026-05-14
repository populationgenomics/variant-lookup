"""Tests for the RefSeq index."""

import json
from pathlib import Path

from variant_lookup.refseq import GeneAccessions, RefSeqIndex


def _index() -> RefSeqIndex:
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


def test_transcript_protein_genomic_lookup() -> None:
    idx = _index()
    assert idx.transcript_for("SLC20A2") == "NM_006749.5"
    assert idx.protein_for("SLC20A2") == "NP_006740.1"
    assert idx.genomic_for("SLC20A2") == "NC_000008.11"


def test_lookup_unknown_gene() -> None:
    idx = _index()
    assert idx.transcript_for("UNKNOWN") is None
    assert idx.protein_for("UNKNOWN") is None
    assert idx.genomic_for("UNKNOWN") is None


def test_versioned_accession_autocomplete() -> None:
    idx = _index()
    assert idx.versioned_accession("NM_006749") == "NM_006749.5"
    assert idx.versioned_accession("NP_002599") == "NP_002599.1"
    assert idx.versioned_accession("NC_000008") == "NC_000008.11"


def test_versioned_accession_passthrough() -> None:
    idx = _index()
    assert idx.versioned_accession("NM_006749.5") == "NM_006749.5"


def test_versioned_accession_unknown() -> None:
    idx = _index()
    assert idx.versioned_accession("NM_999999") is None


def test_mane_version_promotes_stale_caller_version() -> None:
    """Caller-supplied old version → current MANE version (silent substitution)."""
    idx = _index()
    assert idx.mane_version_for_accession("NM_006749.1") == "NM_006749.5"
    assert idx.mane_version_for_accession("NM_006749") == "NM_006749.5"
    # Already MANE: passthrough.
    assert idx.mane_version_for_accession("NM_006749.5") == "NM_006749.5"
    # Not in index: None (caller's value preserved upstream).
    assert idx.mane_version_for_accession("NM_999999.1") is None


def test_entry_for_accession_handles_version_drift() -> None:
    """Entry lookup tolerates a non-MANE version of an indexed base."""
    idx = _index()
    entry = idx.entry_for_accession("NM_006749.1")  # old version
    assert entry is not None and entry.symbol == "SLC20A2"
    entry = idx.entry_for_accession("NP_006740.1")  # protein
    assert entry is not None and entry.symbol == "SLC20A2"
    assert idx.entry_for_accession("NM_999999.1") is None


def test_from_file_round_trip(tmp_path: Path) -> None:
    raw = {
        "SLC20A2": {
            "RNA": "NM_006749.5",
            "Protein": "NP_006740.1",
            "Genomic": "NC_000008.11",
            "Symbol": "SLC20A2",
            "GeneID": "6575",
            "MANE": True,
        }
    }
    path = tmp_path / "refseq.json"
    path.write_text(json.dumps(raw))
    idx = RefSeqIndex.from_file(path)
    assert idx.transcript_for("SLC20A2") == "NM_006749.5"
    assert idx.versioned_accession("NM_006749") == "NM_006749.5"


def test_from_file_handles_missing_fields(tmp_path: Path) -> None:
    raw = {"GENE": {"Symbol": "GENE"}}
    path = tmp_path / "refseq.json"
    path.write_text(json.dumps(raw))
    idx = RefSeqIndex.from_file(path)
    assert idx.transcript_for("GENE") is None
    assert idx.protein_for("GENE") is None
