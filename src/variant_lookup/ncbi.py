"""NCBI E-utilities client — used only for rsID → HGVS resolution.

The only external HTTP dependency we keep. dbSNP is ~hundreds of GB
self-hosted, and the HGVS strings aren't materialized in the raw VCF dump
anyway (they're computed by NCBI from RefSeq alignments). With an API key
the rate limit is 10 req/s, ample for the rare rsID inputs we see.
"""

from dataclasses import dataclass
from typing import Any

import httpx
from defusedxml import ElementTree as _DefusedET

_EUTILS_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
_DOCSUM_NS = "{https://www.ncbi.nlm.nih.gov/SNP/docsum}"


class NCBIError(Exception):
    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}" if message else code)


@dataclass(frozen=True)
class RsIDResolution:
    hgvs_c: str | None
    hgvs_p: str | None
    hgvs_g: str | None
    gene: str | None


def resolve_rsid(
    rsid: str,
    *,
    email: str,
    api_key: str | None = None,
    timeout: float = 10.0,
) -> RsIDResolution:
    """Resolve an rsID (``rsNNNN``) to its HGVS-c / HGVS-p / HGVS-g strings."""
    if not rsid.startswith("rs") or not rsid[2:].isnumeric():
        raise NCBIError("INVALID_RSID", f"not a valid rsID: {rsid!r}")

    params: dict[str, str] = {
        "db": "snp",
        "id": rsid[2:],
        "retmode": "xml",
        "rettype": "xml",
        "tool": "variant-lookup",
        "email": email,
    }
    if api_key:
        params["api_key"] = api_key

    try:
        response = httpx.get(_EUTILS_URL, params=params, timeout=timeout)
        response.raise_for_status()
    except httpx.TimeoutException as e:
        raise NCBIError("UPSTREAM_TIMEOUT", str(e)) from e
    except httpx.HTTPError as e:
        raise NCBIError("UPSTREAM_ERROR", str(e)) from e

    root = _DefusedET.fromstring(response.text)
    return _parse_docsum(root, rsid)


def _parse_docsum(root: Any, rsid: str) -> RsIDResolution:
    uid = rsid[2:]
    node = next(
        iter(root.findall(f"./{_DOCSUM_NS}DocumentSummary[@uid='{uid}']/{_DOCSUM_NS}DOCSUM")),
        None,
    )
    if node is None or not node.text:
        return RsIDResolution(None, None, None, None)

    props = dict(kvp.split("=", 1) for kvp in (node.text or "").split("|") if "=" in kvp)
    hgvs_list = [h for h in props.get("HGVS", "").split(",") if h]
    gene_raw = props.get("GENE", "").split(":")[0]

    return RsIDResolution(
        hgvs_c=next((h for h in hgvs_list if h.startswith("NM_")), None),
        hgvs_p=next((h for h in hgvs_list if h.startswith("NP_")), None),
        hgvs_g=next((h for h in hgvs_list if h.startswith("NC_")), None),
        gene=gene_raw or None,
    )
