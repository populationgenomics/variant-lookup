"""HTTP client for the sibling ``mutalyzer-api`` container.

Mutalyzer 3 (MIT) runs as its own service rather than in-process in the
gateway, mirroring how we already speak to VariantValidator. The boundary
isn't license-driven (mutalyzer is MIT both sides) — it's operational:

  - the per-process reference-sequence LRU stays in one place, scaling with
    mutalyzer-api worker count rather than gateway worker count;
  - the gateway image stays lean (no biopython, no mutalyzer-retriever);
  - replacing mutalyzer later is a URL change, not a refactor;
  - the existing nginx + uvicorn worker config can scale gateway-side
    concurrency without touching the in-memory cache footprint.

Frameshift normalization is intentionally **not** delegated: upstream
Mutalyzer doesn't normalize frameshift descriptions, so we apply
healthfutures-evagg's canonicalization locally and short-circuit before
hitting the API.
"""

import re
from typing import Any, cast
from urllib.parse import quote

import httpx


class MutalyzerError(Exception):
    """Mutalyzer returned an error response, or its HTTP API was unreachable."""

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}" if message else code)


_FS_PATTERN = re.compile(r"fs")

# Protein single-letter → three-letter amino-acid code (for fs canonicalization).
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


def _normalize_frameshift(hgvs: str) -> dict[str, Any]:
    refseq, hgvs_desc = hgvs.split(":", 1)
    hgvs_desc = re.sub(r"(\(?)([A-Za-z]+[0-9]+)[A-Za-z0-9*]+(\)?)", r"\1\2fs\3", hgvs_desc)
    match = re.match(r"(p\.\(?)([A-Z])([0-9]+fs\)?)", hgvs_desc)
    if match:
        hgvs_desc = match.group(1) + _PROTEIN_LETTERS_1TO3[match.group(2)] + match.group(3)
    return {"normalized_description": f"{refseq}:{hgvs_desc}"}


def _extract_error(response: dict[str, Any]) -> tuple[str, str] | None:
    errors = response.get("errors") or response.get("custom", {}).get("errors")
    if not errors:
        return None
    err = errors[0]
    return err.get("code", "UNKNOWN"), err.get("details", "")


def _structured_422(response: httpx.Response) -> "MutalyzerError":
    """Promote a Mutalyzer 422 to a typed error with the structured upstream code.

    Mutalyzer signals input-level problems with HTTP 422 carrying a JSON body
    of the form ``{"errors": [{"code": "EINTRONIC", "details": "..."}], ...}``.
    Pulling that code through gives callers an actionable error
    (``NORMALIZATION_EINTRONIC`` etc.) instead of an opaque
    ``UPSTREAM_ERROR: Client error '422 …'``.
    """
    try:
        body = cast("dict[str, Any]", response.json())
    except ValueError:
        return MutalyzerError("UPSTREAM_ERROR", f"422 (non-JSON body): {response.text[:200]}")
    error = _extract_error(body)
    if error:
        return MutalyzerError(code=error[0], message=error[1])
    return MutalyzerError("UPSTREAM_ERROR", "422 with no errors[] in body")


def _trim(response: dict[str, Any]) -> dict[str, Any]:
    """Return only the fields the pipeline cares about."""
    out: dict[str, Any] = {}
    if "normalized_description" in response:
        out["normalized_description"] = response["normalized_description"]
    protein = response.get("protein")
    if isinstance(protein, dict) and "description" in protein:
        out["protein"] = {"description": protein["description"]}
    if "equivalent_descriptions" in response:
        out["equivalent_descriptions"] = response["equivalent_descriptions"]
    return out


class MutalyzerClient:
    """HTTP client for the sibling mutalyzer-api service."""

    def __init__(self, base_url: str, *, timeout: float = 150.0) -> None:
        # 150 s gives the cold-fetch path room (chromosomal-scale FASTA from
        # NCBI over the cross-Pacific link can take 60-90 s) without hitting
        # nginx's 180 s proxy_read_timeout.
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def normalize_raw(self, hgvs: str) -> dict[str, Any]:
        """Return the upstream ``/api/normalize/<description>`` response unchanged.

        For frameshift inputs short-circuits to our local canonicalization
        without hitting the API (upstream doesn't normalize frameshifts).
        """
        if _FS_PATTERN.search(hgvs.split(":", 1)[-1]):
            return _normalize_frameshift(hgvs)
        url = f"{self._base_url}/api/normalize/{quote(hgvs, safe='')}"
        try:
            response = httpx.get(url, timeout=self._timeout)
        except httpx.TimeoutException as e:
            raise MutalyzerError("UPSTREAM_TIMEOUT", str(e)) from e
        except httpx.HTTPError as e:
            raise MutalyzerError("UPSTREAM_ERROR", str(e)) from e
        if response.status_code == 422:
            raise _structured_422(response)
        try:
            response.raise_for_status()
        except httpx.HTTPError as e:
            raise MutalyzerError("UPSTREAM_ERROR", str(e)) from e
        return cast("dict[str, Any]", response.json())

    def normalize(self, hgvs: str) -> dict[str, Any]:
        """Return a trimmed normalize response; raise :class:`MutalyzerError` on failure."""
        response = self.normalize_raw(hgvs)
        error = _extract_error(response)
        if error:
            raise MutalyzerError(code=error[0], message=error[1])
        return _trim(response)

    def back_translate(self, hgvsp: str) -> list[str]:
        """Back-translate a protein HGVS description to coding-variant alternatives."""
        if _FS_PATTERN.search(hgvsp.split(":", 1)[-1]):
            raise MutalyzerError(
                "FRAMESHIFT_UNSUPPORTED",
                "back-translation of frameshift variants not supported",
            )
        url = f"{self._base_url}/api/back_translate/{quote(hgvsp, safe='')}"
        try:
            response = httpx.get(url, timeout=self._timeout)
        except httpx.TimeoutException as e:
            raise MutalyzerError("UPSTREAM_TIMEOUT", str(e)) from e
        except httpx.HTTPError as e:
            raise MutalyzerError("UPSTREAM_ERROR", str(e)) from e
        if response.status_code == 422:
            raise _structured_422(response)
        try:
            response.raise_for_status()
        except httpx.HTTPError as e:
            raise MutalyzerError("UPSTREAM_ERROR", str(e)) from e
        result = response.json()
        if not isinstance(result, list):
            raise MutalyzerError(
                "UPSTREAM_ERROR",
                f"expected list from back_translate, got {type(result).__name__}",
            )
        return [str(x) for x in result]
