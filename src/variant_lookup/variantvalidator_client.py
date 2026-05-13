"""VariantValidator HTTP client.

VV is AGPL-3.0-only. We never import VV code in-process; the gateway speaks
to the sibling container over HTTP only. See ARCHITECTURE.md § "AGPL boundary".
"""

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx


class VariantValidatorError(Exception):
    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}" if message else code)


@dataclass(frozen=True)
class VVResult:
    pseudo_vcf: str  # chrom-pos-ref-alt, GRCh38
    hgvs_c: str
    hgvs_p: str


class VariantValidatorClient:
    """Synchronous client for the sibling VV REST service."""

    def __init__(self, base_url: str, *, timeout: float = 30.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def mane_select(self, hgvs: str, *, genome_build: str = "GRCh38") -> VVResult:
        """Resolve an HGVS description to a GRCh38 pseudo-VCF + MANE-select HGVS-c/p.

        Asks VV for the MANE-select transcript regardless of what the caller
        supplied. The output is always GRCh38; GRCh37 chromosomal inputs are
        projected by VV internally.
        """
        encoded = quote(hgvs, safe="")
        url = (
            f"{self._base_url}/VariantValidator/variantvalidator/"
            f"{genome_build}/{encoded}/mane_select"
        )
        try:
            response = httpx.get(url, timeout=self._timeout)
            response.raise_for_status()
        except httpx.HTTPError as e:
            raise VariantValidatorError("UPSTREAM_ERROR", str(e)) from e

        data = response.json()
        entry = self._find_entry(data, hgvs)
        vcf = entry.get("primary_assembly_loci", {}).get("grch38", {}).get("vcf")
        if not vcf:
            raise VariantValidatorError(
                "NO_GENOMIC_COORDS",
                f"VV returned no GRCh38 coords for {hgvs!r}",
            )

        hgvs_p_raw = entry.get("hgvs_predicted_protein_consequence", {}).get("tlr", "")
        return VVResult(
            pseudo_vcf=f"{vcf['chr']}-{vcf['pos']}-{vcf['ref']}-{vcf['alt']}",
            hgvs_c=entry["hgvs_transcript_variant"],
            hgvs_p=_strip_parens(hgvs_p_raw),
        )

    @staticmethod
    def _find_entry(data: dict[str, Any], hgvs: str) -> dict[str, Any]:
        """VV keys responses by the resolved transcript variant, not the
        caller-supplied description. Walk the keys to find the right entry.
        """
        if hgvs in data:
            return data[hgvs]  # type: ignore[no-any-return]
        if hgvs.startswith("NC_") and "m." in hgvs:
            mito = data.get("mitochondrial_variant_1")
            if mito is not None:
                return mito  # type: ignore[no-any-return]
        # Genomic-wrapped HGVS like NC_000007.14(NM_003592.3):c.483+1G>A
        if "(" in hgvs and ":" in hgvs:
            transcript = hgvs.split("(", 1)[1].split(")", 1)[0]
            desc = hgvs.split(":", 1)[1]
            key = f"{transcript}:{desc}"
            if key in data:
                return data[key]  # type: ignore[no-any-return]
        # Genomic g. inputs come back keyed by the resolved transcript variant.
        for key, value in data.items():
            if key.startswith("NM_"):
                return value  # type: ignore[no-any-return]
        return {}


def _strip_parens(s: str) -> str:
    return s.replace("(", "").replace(")", "")
