"""Cleanup of messy LLM-extracted variant text into syntactically-valid HGVS.

This phase does NOT call any external service. It only rewrites the input
string into something Mutalyzer / VariantValidator can accept downstream,
and assigns a refseq (chromosomally derived for ``g.`` variants, predicted
from the gene's MANE-Select for ``c.``/``p.`` variants, or extracted from
the input itself if the caller provided one).

Lifted, with simplifications, from Microsoft's healthfutures-evagg (MIT).
"""

import re
from dataclasses import dataclass

from variant_lookup.refseq import RefSeqIndex

# Chromosome → NC_ accession mapping per genome build.
_CHR_TO_NC: dict[str, dict[str, str]] = {
    "GRCh37": {
        "chr1": "NC_000001.10",
        "chr2": "NC_000002.11",
        "chr3": "NC_000003.11",
        "chr4": "NC_000004.11",
        "chr5": "NC_000005.9",
        "chr6": "NC_000006.11",
        "chr7": "NC_000007.13",
        "chr8": "NC_000008.10",
        "chr9": "NC_000009.11",
        "chr10": "NC_000010.10",
        "chr11": "NC_000011.9",
        "chr12": "NC_000012.11",
        "chr13": "NC_000013.10",
        "chr14": "NC_000014.8",
        "chr15": "NC_000015.9",
        "chr16": "NC_000016.9",
        "chr17": "NC_000017.10",
        "chr18": "NC_000018.9",
        "chr19": "NC_000019.9",
        "chr20": "NC_000020.10",
        "chr21": "NC_000021.8",
        "chr22": "NC_000022.10",
        "chrX": "NC_000023.10",
        "chrY": "NC_000024.9",
    },
    "GRCh38": {
        "chr1": "NC_000001.11",
        "chr2": "NC_000002.12",
        "chr3": "NC_000003.12",
        "chr4": "NC_000004.12",
        "chr5": "NC_000005.10",
        "chr6": "NC_000006.12",
        "chr7": "NC_000007.14",
        "chr8": "NC_000008.11",
        "chr9": "NC_000009.12",
        "chr10": "NC_000010.11",
        "chr11": "NC_000011.10",
        "chr12": "NC_000012.12",
        "chr13": "NC_000013.11",
        "chr14": "NC_000014.9",
        "chr15": "NC_000015.10",
        "chr16": "NC_000016.10",
        "chr17": "NC_000017.11",
        "chr18": "NC_000018.10",
        "chr19": "NC_000019.10",
        "chr20": "NC_000020.11",
        "chr21": "NC_000021.9",
        "chr22": "NC_000022.11",
        "chrX": "NC_000023.11",
        "chrY": "NC_000024.10",
    },
}

_BUILD_ALIASES: dict[str, str] = {"hg19": "GRCh37", "hg38": "GRCh38"}

# Mitochondrial reference — same in both builds.
_MITO_REFSEQ = "NC_012920.1"

# Protein single-letter → three-letter amino-acid code.
_PROTEIN_LETTERS_1TO3: dict[str, str] = {
    "A": "Ala",
    "C": "Cys",
    "D": "Asp",
    "E": "Glu",
    "F": "Phe",
    "G": "Gly",
    "H": "His",
    "I": "Ile",
    "K": "Lys",
    "L": "Leu",
    "M": "Met",
    "N": "Asn",
    "P": "Pro",
    "Q": "Gln",
    "R": "Arg",
    "S": "Ser",
    "T": "Thr",
    "V": "Val",
    "W": "Trp",
    "Y": "Tyr",
}


class VariantCleanupError(ValueError):
    """The input couldn't be cleaned into a recognizable HGVS form."""


@dataclass(frozen=True)
class CleanedVariant:
    """A syntactically-valid HGVS variant ready for Mutalyzer / VariantValidator."""

    refseq: str
    hgvs_desc: str

    def __str__(self) -> str:
        return f"{self.refseq}:{self.hgvs_desc}"


# --- rsID handling --------------------------------------------------------


_RSID_PATTERN = re.compile(r".*?:?(rs\d+).*?")


def extract_rsid(text: str) -> str | None:
    """Return an rsID if the text contains one (``rs\\d+``), otherwise None.

    rsID resolution requires an NCBI call and is handled separately by the
    pipeline — this function just classifies the input.
    """
    match = _RSID_PATTERN.match(text)
    return match.group(1) if match else None


# --- HGVS cleanup ---------------------------------------------------------


def _chr_to_nc_accession(chr_refseq: str, genome_build: str | None) -> str:
    if not genome_build:
        raise VariantCleanupError(f"chromosome-style refseq '{chr_refseq}' requires a genome build")
    normalized_build = _BUILD_ALIASES.get(genome_build, genome_build)
    build_map = _CHR_TO_NC.get(normalized_build)
    if not build_map:
        raise VariantCleanupError(f"unknown genome build '{genome_build}'")
    chr_match = re.match(r"(chr[\dXY]+)", chr_refseq, re.IGNORECASE)
    if not chr_match:
        raise VariantCleanupError(f"cannot parse chromosome from refseq '{chr_refseq}'")
    chr_key = chr_match.group(1).lower()
    nc = next((v for k, v in build_map.items() if k.lower() == chr_key), None)
    if not nc:
        raise VariantCleanupError(f"unknown chromosome '{chr_key}' for build '{normalized_build}'")
    return nc


def _predict_refseq(
    hgvs_desc: str,
    gene_symbol: str | None,
    refseq_index: RefSeqIndex,
) -> str | None:
    """Predict a refseq when the input didn't include one."""
    if hgvs_desc.startswith("m."):
        return _MITO_REFSEQ
    if hgvs_desc.startswith("g."):
        raise VariantCleanupError("genomic (g.) variants must include a refseq; none provided")
    if not gene_symbol:
        return None
    if hgvs_desc.startswith("p."):
        protein = refseq_index.protein_for(gene_symbol)
        transcript = refseq_index.transcript_for(gene_symbol)
        if transcript and protein:
            return f"{transcript}({protein})"
        return protein
    if hgvs_desc.startswith("c."):
        return refseq_index.transcript_for(gene_symbol)
    return None


def _ensure_versioned(refseq: str, refseq_index: RefSeqIndex) -> str:
    if "." in refseq:
        return refseq
    versioned = refseq_index.versioned_accession(refseq)
    return versioned if versioned else refseq


def _cleanup_hgvs_desc(text: str, is_chromosomal: bool) -> str:
    """Apply per-variant-string heuristics that produce HGVS-shaped output."""
    text = text.replace("(", "").replace(")", "").replace("[", "").replace("]", "")
    text = text.split(";")[0]
    text = text.split(":")[-1]

    if re.search(r"^[A-Za-z]+\d+[A-Za-z]+$", text):
        text = "p." + text

    bare_prefix = "g." if is_chromosomal else "c."
    if re.search(r"^\d+[ACGT]>[ACGT]$", text):
        text = bare_prefix + text
    if re.search(r"^\d+(_\d+)?del[ACGT]*$", text):
        text = bare_prefix + text
    if re.search(r"^\d+ins[ACGT]*$", text):
        text = bare_prefix + text

    # Stop-codon glyphs.
    text = re.sub(r"(p\.[A-Z]\d+)X", r"\1*", text)
    text = re.sub(r"(p\.[A-Z]\d+)stop", r"\1*", text)

    # c.{ref}{pos}{alt} → c.{pos}{ref}>{alt}
    text = re.sub(r"c\.([ACTG])(\d+)([A-Z]+)", r"c.\2\1>\3", text)

    # Capitalize three-letter p. amino-acid codes.
    if "del" not in text:
        match = re.match(r"p\.([A-Za-z][a-z]{2})(\d+)([A-Za-z][a-z]{2})*(.*?)$", text)
        if match:
            ref_aa, pos, alt_aa, extra = match.groups()
            text = f"p.{ref_aa.capitalize()}{pos}{alt_aa.capitalize() if alt_aa else ''}{extra}"

    text = text.replace("frameshift", "fs")

    # Stray hyphens.
    text = re.sub(r"(?<!\d)-(?!\d)", "", text)

    # Frameshift: drop everything after "fs".
    if "fs" in text:
        text = text.split("fs")[0] + "fs"

    return text


def clean(
    variant_text: str,
    gene_symbol: str | None,
    genome_build: str | None,
    refseq_index: RefSeqIndex,
) -> CleanedVariant:
    """Apply cleanup heuristics; return a refseq + hgvs_desc tuple ready for Mutalyzer.

    Raises :class:`VariantCleanupError` if the input can't be parsed.
    Use :func:`extract_rsid` first to route rsID inputs to the NCBI path
    (this function rejects them).
    """
    if extract_rsid(variant_text):
        raise VariantCleanupError("rsID inputs are not cleaned here; use extract_rsid")

    text = variant_text.replace(" ", "")

    # Strip embedded gene-symbol prefixes: SLC20A2:c.1240G>T, SLC20A2(c.1240G>T).
    if gene_symbol:
        text = re.sub(f"g?{gene_symbol}:", "", text)
        match = re.search(r"g?" + gene_symbol + r"\((.*?)\)", text)
        if match:
            text = match.group(1)

    # Split refseq if present (e.g. NM_001257180.1:c.1240G>T).
    if ":" in text:
        refseq, text = text.split(":", 1)
        refseq = refseq.strip()
        text = text.strip()
    else:
        refseq = ""
        text = text.strip()

    if not re.search(r"[A-Za-z]", text):
        raise VariantCleanupError(f"variant string '{variant_text}' appears unparsable")

    is_chromosomal = bool(refseq) and "chr" in refseq.lower()
    if is_chromosomal:
        refseq = _chr_to_nc_accession(refseq, genome_build)
    elif refseq and not re.match(r"(NM_|NP_|NC_)", refseq):
        # Ignore garbage refseq prefixes; we'll predict one below.
        refseq = ""

    hgvs_desc = _cleanup_hgvs_desc(text, is_chromosomal)

    if not refseq:
        predicted = _predict_refseq(hgvs_desc, gene_symbol, refseq_index)
        if not predicted:
            if not gene_symbol:
                raise VariantCleanupError(
                    f"variant '{variant_text}' needs either a RefSeq prefix "
                    f"(NC_/NM_/NP_:...) or a gene symbol to resolve a transcript"
                )
            raise VariantCleanupError(
                f"no MANE-Select transcript known for gene '{gene_symbol}' "
                f"(needed to resolve '{variant_text}')"
            )
        refseq = predicted
    else:
        refseq = _ensure_versioned(refseq, refseq_index)

    return CleanedVariant(refseq=refseq, hgvs_desc=hgvs_desc)
