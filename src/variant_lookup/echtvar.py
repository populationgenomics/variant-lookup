"""echtvar subprocess wrapper for offline gnomAD frequency lookups.

The encoded data is sharded by chromosome — one archive per chrom, named
``gnomad.joint.v{version}.chr{chrom}.echtvar.zip``. Per-chrom archives are
the canonical form: the underlying string-id tables for categorical fields
are per-archive (insertion-order indices differ across chromosomes), so a
merged single-archive form is not safe without a stream-vbyte-aware index
remap. ``annotate`` groups variants by chromosome, then dispatches one
subprocess per chromosome in parallel, each against the matching archive.
"""

import gzip
import subprocess
import tempfile
from collections import defaultdict
from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import NamedTuple

from variant_lookup.models import Frequency

# GRCh38 PAR regions on chrX (1-based, inclusive).
_PAR_REGIONS_X: tuple[tuple[int, int], ...] = (
    (10_001, 2_781_479),
    (155_701_383, 156_030_895),
)

# echtvar's default missing-value sentinels in the annotated VCF.
_MISSING_INT = -1
_MISSING_CATEGORICAL = "MISSING"


class PseudoVCF(NamedTuple):
    chrom: str
    pos: int
    ref: str
    alt: str

    @classmethod
    def parse(cls, s: str) -> "PseudoVCF":
        chrom, pos_s, ref, alt = s.split("-", 3)
        return cls(chrom.removeprefix("chr"), int(pos_s), ref, alt)


def _archive_path(archives_dir: Path, gnomad_version: str, chrom: str) -> Path:
    return archives_dir / f"gnomad.joint.v{gnomad_version}.chr{chrom}.echtvar.zip"


def _hemizygote_count(chrom: str, pos: int, ac_xy: int) -> int:
    """Hemizygote count: AC_XY on non-PAR chrX/Y, else 0."""
    if chrom not in ("X", "Y"):
        return 0
    if chrom == "X" and any(start <= pos <= end for start, end in _PAR_REGIONS_X):
        return 0
    return max(ac_xy, 0)


def _write_vcf(path: Path, chrom: str, variants: Iterable[PseudoVCF]) -> None:
    """Write a minimal valid VCF for echtvar input. Variants must already be sorted by pos."""
    lines: list[str] = [
        "##fileformat=VCFv4.2",
        f"##contig=<ID=chr{chrom}>",
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO",
    ]
    for v in variants:
        lines.append(f"chr{v.chrom}\t{v.pos}\t.\t{v.ref}\t{v.alt}\t.\t.\t.")
    path.write_text("\n".join(lines) + "\n")


def _parse_vcf(path: Path) -> dict[tuple[str, int, str, str], dict[str, str]]:
    """Read an annotated VCF and return INFO field map keyed by (chrom, pos, ref, alt).

    echtvar's `anno` always writes bgzipped output regardless of the filename's
    extension — it calls rust_htslib's `Writer::from_path(path, header,
    uncompressed=false, ...)`. bgzip is gzip-compatible at the stream level so
    stdlib `gzip.open(..., "rt")` reads it transparently.
    """
    out: dict[tuple[str, int, str, str], dict[str, str]] = {}
    with gzip.open(path, "rt") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line or line.startswith("#"):
                continue
            cols = line.split("\t")
            chrom = cols[0].removeprefix("chr")
            pos = int(cols[1])
            ref, alt, info_field = cols[3], cols[4], cols[7]
            info = dict(item.split("=", 1) for item in info_field.split(";") if "=" in item)
            out[(chrom, pos, ref, alt)] = info
    return out


def _info_to_frequency(info: dict[str, str], chrom: str, pos: int) -> Frequency | None:
    ac_str = info.get("gnomad_ac")
    if ac_str is None or int(ac_str) == _MISSING_INT:
        return None
    ac_xy = int(info.get("gnomad_ac_xy", "0"))
    faf95_str = info.get("gnomad_faf95_max")
    faf95 = float(faf95_str) if faf95_str is not None and float(faf95_str) >= 0 else None
    gen_anc = info.get("gnomad_faf95_max_gen_anc")
    if gen_anc in (None, "", _MISSING_CATEGORICAL):
        gen_anc = None
    return Frequency(
        ac=int(ac_str),
        an=int(info["gnomad_an"]),
        homozygote_count=int(info.get("gnomad_nhomalt", "0")),
        hemizygote_count=_hemizygote_count(chrom, pos, ac_xy),
        faf95_popmax=faf95,
        faf95_popmax_population=gen_anc,
    )


def _annotate_one_chrom(
    chrom: str,
    variants: list[PseudoVCF],
    *,
    archive: Path,
    binary: str,
    tmp: Path,
) -> dict[tuple[str, int, str, str], dict[str, str]]:
    """Run echtvar against a single per-chromosome archive. Returns annotation map."""
    if not archive.is_file():
        return {}
    sorted_variants = sorted(variants, key=lambda pv: pv.pos)
    input_vcf = tmp / f"input_chr{chrom}.vcf"
    output_vcf = tmp / f"output_chr{chrom}.vcf"
    _write_vcf(input_vcf, chrom, sorted_variants)
    subprocess.run(
        [binary, "anno", "-e", str(archive), str(input_vcf), str(output_vcf)],
        check=True,
        capture_output=True,
        text=True,
    )
    return _parse_vcf(output_vcf)


def annotate(
    variants: Sequence[str],
    *,
    archives_dir: Path,
    gnomad_version: str,
    binary: str = "echtvar",
) -> list[Frequency | None]:
    """Annotate a list of pseudo-VCF strings against per-chromosome echtvar archives.

    Returns a list parallel to ``variants`` — same length, same order. ``None``
    entries indicate variants not found in gnomAD (or with the missing-value
    sentinel returned by echtvar). Variants whose chromosome has no matching
    archive are silently returned as ``None``.
    """
    parsed = [PseudoVCF.parse(s) for s in variants]
    by_chrom: dict[str, list[PseudoVCF]] = defaultdict(list)
    for pv in parsed:
        by_chrom[pv.chrom].append(pv)

    annotations: dict[tuple[str, int, str, str], dict[str, str]] = {}
    with tempfile.TemporaryDirectory(prefix="echtvar-") as tmp_str:
        tmp = Path(tmp_str)
        if by_chrom:
            with ThreadPoolExecutor(max_workers=len(by_chrom)) as pool:
                results = pool.map(
                    lambda item: _annotate_one_chrom(
                        item[0],
                        item[1],
                        archive=_archive_path(archives_dir, gnomad_version, item[0]),
                        binary=binary,
                        tmp=tmp,
                    ),
                    by_chrom.items(),
                )
                for chrom_annotations in results:
                    annotations.update(chrom_annotations)

    return [
        _info_to_frequency(
            annotations.get((pv.chrom, pv.pos, pv.ref, pv.alt), {}), pv.chrom, pv.pos
        )
        for pv in parsed
    ]
