"""Build refseq_processed.json from the NCBI RefSeq GRCh38 GFF.

Run via setup.sh ``refresh-refseq``, which invokes us inside the gateway
image so the host doesn't need to install anything beyond docker:

    docker run --rm -v ${DATA_DIR}/refseq:/data/refseq \\
        variant-lookup-gateway:latest \\
        python -m variant_lookup.refseq_build /data/refseq/refseq_processed.json

Direct invocation (Python only) is also supported for local development:

    python -m variant_lookup.refseq_build /tmp/refseq_processed.json

Lifted, with simplifications, from Microsoft's healthfutures-evagg (MIT).
"""

import gzip
import json
import re
import sys
import urllib.request
from pathlib import Path
from typing import Any

_NCBI_REFSEQ_URL = (
    "https://ftp.ncbi.nlm.nih.gov/genomes/refseq/vertebrate_mammalian/"
    "Homo_sapiens/all_assembly_versions/GCF_000001405.39_GRCh38.p13/"
    "GCF_000001405.39_GRCh38.p13_genomic.gff.gz"
)


def download_gff(target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(_NCBI_REFSEQ_URL) as resp, target.open("wb") as out:
        while chunk := resp.read(64 * 1024):
            out.write(chunk)


def process_gff(gff_path: Path) -> dict[str, dict[str, Any]]:
    """Extract MANE-Select / RefSeq-Select transcript + protein + genomic accessions.

    Two-pass: collect CDS and mRNA lines first, then process proteins
    before transcripts. A single in-order pass loses every transcript —
    in GFF3 the mRNA feature for each gene precedes its CDS, so
    ``_record_transcript`` would skip on ``gene not in refseqs`` for
    everything.
    """
    protein_lines: list[str] = []
    transcript_lines: list[str] = []
    with gzip.open(gff_path, "rt") as f:
        for line in f:
            if not re.search(r"(MANE|RefSeq) Select", line):
                continue
            if re.search(r"BestRefSeq\s+CDS", line):
                protein_lines.append(line)
            elif re.search(r"BestRefSeq\s+mRNA", line):
                transcript_lines.append(line)
    refseqs: dict[str, dict[str, Any]] = {}
    for line in protein_lines:
        _record_protein(line, refseqs)
    for line in transcript_lines:
        _record_transcript(line, refseqs)
    return refseqs


def _parse_attributes(line: str) -> dict[str, str]:
    tokens = line.rstrip("\n").split("\t")
    return dict(kv.split("=", 1) for kv in tokens[8].split(";") if "=" in kv)


def _record_protein(line: str, refseqs: dict[str, dict[str, Any]]) -> None:
    tokens = line.split("\t")
    attrs = _parse_attributes(line)
    if "gene" not in attrs or "Select" not in attrs.get("tag", ""):
        return
    gene = attrs["gene"]
    is_mane = "MANE Select" in attrs.get("tag", "")
    if gene in refseqs and refseqs[gene]["MANE"]:
        return  # already have MANE for this gene; don't downgrade
    xref = dict(kv.split(":", 1) for kv in attrs.get("Dbxref", "").split(",") if ":" in kv)
    refseqs[gene] = {
        "Protein": attrs.get("protein_id"),
        "Genomic": tokens[0],
        "Symbol": gene,
        "GeneID": xref.get("GeneID"),
        "MANE": is_mane,
    }


def _record_transcript(line: str, refseqs: dict[str, dict[str, Any]]) -> None:
    attrs = _parse_attributes(line)
    if "gene" not in attrs or "Select" not in attrs.get("tag", ""):
        return
    gene = attrs["gene"]
    if gene not in refseqs or "RNA" in refseqs[gene]:
        return
    refseqs[gene]["RNA"] = attrs.get("transcript_id", "").strip()


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        print("usage: python -m variant_lookup.refseq_build <output_path>", file=sys.stderr)
        return 2

    output = Path(args[0])
    gff = output.parent / "refseq.gff.gz"

    if not gff.exists():
        print(f"==> downloading RefSeq GFF to {gff}", file=sys.stderr)
        download_gff(gff)
    else:
        print(f"==> {gff} already present, skipping download", file=sys.stderr)

    print("==> processing GFF", file=sys.stderr)
    refseqs = process_gff(gff)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(refseqs, indent=2, sort_keys=True))
    print(f"==> wrote {len(refseqs)} gene entries to {output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
