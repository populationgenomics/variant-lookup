"""Tests for the echtvar subprocess wrapper."""

import subprocess
from pathlib import Path

import pytest

from variant_lookup import echtvar
from variant_lookup.echtvar import PseudoVCF, _chrom_sort_key, _hemizygote_count

# --- pure helpers (no subprocess) -----------------------------------------


class TestPseudoVCFParse:
    def test_basic(self) -> None:
        assert PseudoVCF.parse("8-42437272-C-A") == PseudoVCF(
            chrom="8", pos=42437272, ref="C", alt="A"
        )

    def test_strips_chr_prefix(self) -> None:
        assert PseudoVCF.parse("chr8-42437272-C-A").chrom == "8"


class TestChromSortKey:
    def test_natural_order(self) -> None:
        chroms = ["10", "2", "X", "1", "22", "Y", "11"]
        assert sorted(chroms, key=_chrom_sort_key) == ["1", "2", "10", "11", "22", "X", "Y"]


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


def test_annotate_sorts_input_naturally(monkeypatch, tmp_path) -> None:
    captured: dict[str, str] = {}

    def capturing_run(args, **kwargs):
        captured["input"] = Path(args[-2]).read_text()
        # Defer the rest to the no-hits fake
        return _fake_echtvar({})(args, **kwargs)

    monkeypatch.setattr("variant_lookup.echtvar.subprocess.run", capturing_run)
    echtvar.annotate(
        ["10-100-A-G", "1-50-C-T", "X-200-G-A"],
        archive=tmp_path / "dummy.zip",
    )

    body_chroms = [
        line.split("\t")[0]
        for line in captured["input"].splitlines()
        if line and not line.startswith("#")
    ]
    assert body_chroms == ["chr1", "chr10", "chrX"]


def test_annotate_preserves_request_order(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("variant_lookup.echtvar.subprocess.run", _fake_echtvar({}))
    result = echtvar.annotate(
        ["10-100-A-G", "1-50-C-T", "X-200-G-A"],
        archive=tmp_path / "dummy.zip",
    )
    assert len(result) == 3
    assert all(freq is None for freq in result)


def test_annotate_returns_frequency_for_found_variant(monkeypatch, tmp_path) -> None:
    annotations = {
        ("8", 42437272, "C", "A"): (
            "gnomad_ac=5;gnomad_an=1614174;gnomad_nhomalt=0;"
            "gnomad_ac_xy=2;gnomad_faf95_max=0.0001;gnomad_faf95_max_gen_anc=nfe"
        ),
    }
    monkeypatch.setattr("variant_lookup.echtvar.subprocess.run", _fake_echtvar(annotations))

    result = echtvar.annotate(["8-42437272-C-A"], archive=tmp_path / "dummy.zip")
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
    annotations = {
        ("X", 1_000_000, "A", "G"): "gnomad_ac=3;gnomad_an=100;gnomad_nhomalt=0;gnomad_ac_xy=3",
        ("X", 50_000_000, "A", "G"): "gnomad_ac=3;gnomad_an=100;gnomad_nhomalt=0;gnomad_ac_xy=3",
    }
    monkeypatch.setattr("variant_lookup.echtvar.subprocess.run", _fake_echtvar(annotations))

    par, non_par = echtvar.annotate(
        ["X-1000000-A-G", "X-50000000-A-G"],
        archive=tmp_path / "dummy.zip",
    )
    assert par is not None and par.hemizygote_count == 0
    assert non_par is not None and non_par.hemizygote_count == 3
