"""echtvar subprocess wrapper for offline gnomAD frequency lookups.

Subprocesses the echtvar binary against the local encoded archive.
echtvar reads a sorted VCF and writes annotated INFO fields; we parse
those back into per-variant :class:`Frequency` objects. PAR-aware
hemizygote counts are derived in-process.
"""

import subprocess
import tempfile
from collections.abc import Iterable, Sequence
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


def _chrom_sort_key(chrom: str) -> tuple[int, int, str]:
    """Order 1..22, X, Y naturally — not lexicographically."""
    if chrom.isdigit():
        return (0, int(chrom), "")
    return (1, 0, chrom)


def _hemizygote_count(chrom: str, pos: int, ac_xy: int) -> int:
    """Hemizygote count: AC_XY on non-PAR chrX/Y, else 0."""
    if chrom not in ("X", "Y"):
        return 0
    if chrom == "X" and any(start <= pos <= end for start, end in _PAR_REGIONS_X):
        return 0
    return max(ac_xy, 0)


def _write_vcf(path: Path, variants: Iterable[PseudoVCF]) -> None:
    """Write a minimal valid VCF for echtvar input. Variants must already be sorted."""
    contig_ids = [f"chr{c}" for c in (*[str(i) for i in range(1, 23)], "X", "Y")]
    lines: list[str] = ["##fileformat=VCFv4.2"]
    lines.extend(f"##contig=<ID={c}>" for c in contig_ids)
    lines.append("#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO")
    for v in variants:
        lines.append(f"chr{v.chrom}\t{v.pos}\t.\t{v.ref}\t{v.alt}\t.\t.\t.")
    path.write_text("\n".join(lines) + "\n")


def _parse_vcf(path: Path) -> dict[tuple[str, int, str, str], dict[str, str]]:
    """Read an annotated VCF and return INFO field map keyed by (chrom, pos, ref, alt)."""
    out: dict[tuple[str, int, str, str], dict[str, str]] = {}
    for line in path.read_text().splitlines():
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


def annotate(
    variants: Sequence[str],
    *,
    archive: Path,
    binary: str = "echtvar",
) -> list[Frequency | None]:
    """Annotate a list of pseudo-VCF strings against the local echtvar archive.

    Returns a list parallel to ``variants`` — same length, same order. ``None``
    entries indicate variants not found in gnomAD (or with the missing-value
    sentinel returned by echtvar).
    """
    parsed = [PseudoVCF.parse(s) for s in variants]
    sorted_parsed = sorted(parsed, key=lambda pv: (_chrom_sort_key(pv.chrom), pv.pos))

    with tempfile.TemporaryDirectory(prefix="echtvar-") as tmp_str:
        tmp = Path(tmp_str)
        input_vcf = tmp / "input.vcf"
        output_vcf = tmp / "output.vcf"
        _write_vcf(input_vcf, sorted_parsed)
        subprocess.run(
            [binary, "anno", "-e", str(archive), str(input_vcf), str(output_vcf)],
            check=True,
            capture_output=True,
            text=True,
        )
        annotations = _parse_vcf(output_vcf)

    return [
        _info_to_frequency(
            annotations.get((pv.chrom, pv.pos, pv.ref, pv.alt), {}), pv.chrom, pv.pos
        )
        for pv in parsed
    ]
