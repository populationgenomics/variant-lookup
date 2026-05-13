"""Tests for the echtvar subprocess wrapper."""

import subprocess
from collections.abc import Iterable
from pathlib import Path

import pytest

from variant_lookup import echtvar
from variant_lookup.echtvar import PseudoVCF, _hemizygote_count

# --- pure helpers (no subprocess) -----------------------------------------


class TestPseudoVCFParse:
    def test_basic(self) -> None:
        assert PseudoVCF.parse("8-42437272-C-A") == PseudoVCF(
            chrom="8", pos=42437272, ref="C", alt="A"
        )

    def test_strips_chr_prefix(self) -> None:
        assert PseudoVCF.parse("chr8-42437272-C-A").chrom == "8"


class TestHemizygoteCount:
    def test_autosome_is_zero(self) -> None:
        assert _hemizygote_count("8", 12345, 10) == 0

    def test_par1_is_zero(self) -> None:
        assert _hemizygote_count("X", 1_000_000, 10) == 0

    def test_par2_is_zero(self) -> None:
        assert _hemizygote_count("X", 155_800_000, 10) == 0

    def test_non_par_x_returns_ac_xy(self) -> None:
        assert _hemizygote_count("X", 50_000_000, 7) == 7

    def test_y_returns_ac_xy(self) -> None:
        assert _hemizygote_count("Y", 1234, 3) == 3

    def test_missing_ac_xy_treated_as_zero(self) -> None:
        assert _hemizygote_count("X", 50_000_000, -1) == 0


# --- annotate with mocked subprocess --------------------------------------


def _make_archives(archives_dir: Path, chroms: Iterable[str]) -> None:
    """Place empty placeholder archive files where annotate() expects them.

    annotate() only checks ``is_file()`` before invoking subprocess (the actual
    zip parsing happens in echtvar itself, which we mock). Empty files pass.
    """
    for chrom in chroms:
        (archives_dir / f"gnomad.joint.v4.1.chr{chrom}.echtvar.zip").write_bytes(b"")


def _fake_echtvar(annotations: dict[tuple[str, int, str, str], str]):
    """Return a fake subprocess.run that mirrors echtvar's annotation behavior.

    The fake reads the input VCF, sets each row's INFO field from the provided
    map (keyed by (chrom, pos, ref, alt)), and writes the output VCF.
    """
    missing = (
        "gnomad_ac=-1;gnomad_an=-1;gnomad_nhomalt=-1;"
        "gnomad_ac_xy=-1;gnomad_faf95_max=-1;gnomad_faf95_max_gen_anc=MISSING"
    )

    def fake_run(args, **_kwargs):
        input_vcf = Path(args[-2])
        output_vcf = Path(args[-1])
        lines = []
        for line in input_vcf.read_text().splitlines():
            if not line or line.startswith("#"):
                lines.append(line)
                continue
            cols = line.split("\t")
            key = (cols[0].removeprefix("chr"), int(cols[1]), cols[3], cols[4])
            cols[7] = annotations.get(key, missing)
            lines.append("\t".join(cols))
        output_vcf.write_text("\n".join(lines) + "\n")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    return fake_run


def test_annotate_dispatches_per_chrom_sorted_by_pos(monkeypatch, tmp_path) -> None:
    """One subprocess call per chromosome, each receiving sorted-by-pos variants."""
    _make_archives(tmp_path, ("1", "10", "X"))

    calls: dict[str, list[int]] = {}

    def capturing_run(args, **kwargs):
        input_vcf = Path(args[-2])
        text = input_vcf.read_text()
        contig = next(line for line in text.splitlines() if line.startswith("##contig="))
        chrom = contig.split("=<ID=chr", 1)[1].rstrip(">")
        body_positions = [
            int(line.split("\t")[1])
            for line in text.splitlines()
            if line and not line.startswith("#")
        ]
        calls[chrom] = body_positions
        return _fake_echtvar({})(args, **kwargs)

    monkeypatch.setattr("variant_lookup.echtvar.subprocess.run", capturing_run)
    echtvar.annotate(
        ["10-200-A-G", "1-50-C-T", "10-100-T-G", "X-200-G-A"],
        archives_dir=tmp_path,
        gnomad_version="4.1",
    )

    assert set(calls) == {"1", "10", "X"}
    assert calls["1"] == [50]
    assert calls["10"] == sorted(calls["10"]) == [100, 200]
    assert calls["X"] == [200]


def test_annotate_preserves_request_order(monkeypatch, tmp_path) -> None:
    _make_archives(tmp_path, ("1", "10", "X"))
    monkeypatch.setattr("variant_lookup.echtvar.subprocess.run", _fake_echtvar({}))
    result = echtvar.annotate(
        ["10-100-A-G", "1-50-C-T", "X-200-G-A"],
        archives_dir=tmp_path,
        gnomad_version="4.1",
    )
    assert len(result) == 3
    assert all(freq is None for freq in result)


def test_annotate_returns_frequency_for_found_variant(monkeypatch, tmp_path) -> None:
    _make_archives(tmp_path, ("8",))
    annotations = {
        ("8", 42437272, "C", "A"): (
            "gnomad_ac=5;gnomad_an=1614174;gnomad_nhomalt=0;"
            "gnomad_ac_xy=2;gnomad_faf95_max=0.0001;gnomad_faf95_max_gen_anc=nfe"
        ),
    }
    monkeypatch.setattr("variant_lookup.echtvar.subprocess.run", _fake_echtvar(annotations))

    result = echtvar.annotate(
        ["8-42437272-C-A"],
        archives_dir=tmp_path,
        gnomad_version="4.1",
    )
    assert len(result) == 1
    freq = result[0]
    assert freq is not None
    assert freq.ac == 5
    assert freq.an == 1614174
    assert freq.homozygote_count == 0
    assert freq.hemizygote_count == 0  # autosome
    assert freq.faf95_popmax == pytest.approx(0.0001)
    assert freq.faf95_popmax_population == "nfe"


def test_annotate_chrx_par_hemizygote_zero(monkeypatch, tmp_path) -> None:
    _make_archives(tmp_path, ("X",))
    annotations = {
        ("X", 1_000_000, "A", "G"): "gnomad_ac=3;gnomad_an=100;gnomad_nhomalt=0;gnomad_ac_xy=3",
        ("X", 50_000_000, "A", "G"): "gnomad_ac=3;gnomad_an=100;gnomad_nhomalt=0;gnomad_ac_xy=3",
    }
    monkeypatch.setattr("variant_lookup.echtvar.subprocess.run", _fake_echtvar(annotations))

    par, non_par = echtvar.annotate(
        ["X-1000000-A-G", "X-50000000-A-G"],
        archives_dir=tmp_path,
        gnomad_version="4.1",
    )
    assert par is not None and par.hemizygote_count == 0
    assert non_par is not None and non_par.hemizygote_count == 3


def test_annotate_skips_chrom_without_archive(monkeypatch, tmp_path) -> None:
    """A variant whose chromosome has no archive returns None (no subprocess call)."""
    _make_archives(tmp_path, ("1",))
    calls: list[list[str]] = []

    def capturing_run(args, **kwargs):
        calls.append(list(args))
        return _fake_echtvar({})(args, **kwargs)

    monkeypatch.setattr("variant_lookup.echtvar.subprocess.run", capturing_run)
    result = echtvar.annotate(
        ["1-50-C-T", "Z-100-A-G"],
        archives_dir=tmp_path,
        gnomad_version="4.1",
    )
    assert len(result) == 2
    assert result[1] is None  # chrZ has no archive
    # Only chr1's subprocess was invoked.
    assert len(calls) == 1
