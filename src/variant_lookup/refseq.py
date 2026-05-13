"""RefSeq MANE-Select / Select index — loaded once per process at first use.

The index is built offline by ``scripts/setup.sh refresh-refseq`` (which
shells out to ``python -m variant_lookup.refseq_build``) and persisted as
JSON at the path given by ``Settings.refseq_cache_path``. It maps gene
symbols to the canonical MANE-Select (or RefSeq-Select) transcript,
protein, and genomic accessions, and also lets us resolve versionless
RefSeq accessions to their current versioned form without an NCBI hop.

Lifted, with simplifications, from Microsoft's healthfutures-evagg (MIT).
"""

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from variant_lookup.config import get_settings


@dataclass(frozen=True)
class GeneAccessions:
    symbol: str
    rna: str | None  # MANE-Select transcript, e.g. NM_006749.5
    protein: str | None  # MANE-Select protein,    e.g. NP_006740.1
    genomic: str | None  # Chromosomal NC_ accession, e.g. NC_000008.11
    gene_id: str | None  # NCBI Gene ID
    mane: bool  # True for MANE Select, False for RefSeq Select


class RefSeqIndex:
    """In-memory gene-symbol → accessions index."""

    def __init__(self, entries: dict[str, GeneAccessions]) -> None:
        self._by_gene: dict[str, GeneAccessions] = entries
        # Index versionless accession → versioned form, for autocomplete.
        self._by_unversioned: dict[str, str] = {}
        for entry in entries.values():
            for acc in (entry.rna, entry.protein, entry.genomic):
                if acc and "." in acc:
                    self._by_unversioned[acc.split(".", 1)[0]] = acc

    @classmethod
    def from_file(cls, path: Path) -> "RefSeqIndex":
        with path.open() as f:
            raw: dict[str, dict[str, str | bool | None]] = json.load(f)
        entries = {
            symbol: GeneAccessions(
                symbol=str(info.get("Symbol", symbol)),
                rna=_str_or_none(info.get("RNA")),
                protein=_str_or_none(info.get("Protein")),
                genomic=_str_or_none(info.get("Genomic")),
                gene_id=_str_or_none(info.get("GeneID")),
                mane=bool(info.get("MANE", False)),
            )
            for symbol, info in raw.items()
        }
        return cls(entries)

    def transcript_for(self, gene_symbol: str) -> str | None:
        entry = self._by_gene.get(gene_symbol)
        return entry.rna if entry else None

    def protein_for(self, gene_symbol: str) -> str | None:
        entry = self._by_gene.get(gene_symbol)
        return entry.protein if entry else None

    def genomic_for(self, gene_symbol: str) -> str | None:
        entry = self._by_gene.get(gene_symbol)
        return entry.genomic if entry else None

    def versioned_accession(self, accession: str) -> str | None:
        """Resolve a versionless accession like 'NM_001257180' to its current version."""
        if "." in accession:
            return accession
        return self._by_unversioned.get(accession)


def _str_or_none(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


@lru_cache(maxsize=1)
def get_index() -> RefSeqIndex:
    """Load the refseq index once per process. Path comes from settings."""
    return RefSeqIndex.from_file(get_settings().refseq_cache_path)


def clear_cache() -> None:
    """Drop the cached index — used by tests."""
    get_index.cache_clear()
