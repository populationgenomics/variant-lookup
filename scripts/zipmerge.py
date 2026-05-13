"""Merge multiple per-chromosome echtvar archives into one.

echtvar's encode writes paths `echtvar/<chrom>/<block>/...` plus an
identical `echtvar/config.json`. Per-chromosome paths are disjoint, so
concatenating entries from N per-chrom archives is a valid single
archive that `echtvar anno -e <merged>` reads as if produced in one go.

Caveat: categorical INFO fields produce `echtvar/strings/<alias>.txt`
files whose string-id tables differ per chromosome. Naive merging would
silently lose lookups, so this tool refuses if it sees any such file.
The current gnomAD config is all-numeric, so this never fires in
practice — the check is a guard against future config changes.

Usage:
    python -m zipmerge OUTPUT.zip INPUT1.zip [INPUT2.zip ...]
"""

import sys
import zipfile


def merge(out_path: str, in_paths: list[str]) -> None:
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as out:
        seen: set[str] = set()
        for in_path in in_paths:
            with zipfile.ZipFile(in_path) as src:
                for info in src.infolist():
                    if info.filename.startswith("echtvar/strings/"):
                        raise SystemExit(
                            f"{in_path}: contains {info.filename!r}; merging "
                            "categorical-field strings tables is unsupported."
                        )
                    if info.filename in seen:
                        continue
                    seen.add(info.filename)
                    with src.open(info) as f:
                        out.writestr(info, f.read())


def main() -> None:
    if len(sys.argv) < 3:
        raise SystemExit(f"usage: {sys.argv[0]} OUTPUT.zip INPUT1.zip [INPUT2.zip ...]")
    merge(sys.argv[1], sys.argv[2:])


if __name__ == "__main__":
    main()
