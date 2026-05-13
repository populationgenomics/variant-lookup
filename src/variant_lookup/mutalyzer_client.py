"""In-process Mutalyzer wrapper for HGVS normalization and back-translation.

The Mutalyzer library is MIT-licensed (Leiden UMC), so we import and call
it directly rather than going through an HTTP boundary. See ARCHITECTURE.md
§ "AGPL boundary" for why this differs from how we reach VariantValidator.

Reference-sequence fetching (``mutalyzer-retriever``) hits NCBI on cache
miss and persists results under ``${MUTALYZER_CACHE_DIR}``.

Frameshift normalization is not supported upstream; we apply our own minimal
canonicalization for those, matching healthfutures-evagg's approach.
"""

import os
import re
from pathlib import Path
from typing import Any, cast


def _configure_retriever_cache() -> None:
    """Point mutalyzer-retriever at our cache directory and enable cache writes.

    The retriever reads its config from an in-memory dict (``mutalyzer_retriever
    .configuration.settings``) at call time, not from the environment, so we
    patch the dict at import time. Two keys must both be set, or
    ``mutalyzer_retriever.retriever.get_reference_model`` silently skips writing
    the file cache and every call re-parses the reference from scratch:

      - ``MUTALYZER_CACHE_DIR`` — where to read/write ``<accession>.annotations``
        + ``<accession>.sequence`` files.
      - ``MUTALYZER_FILE_CACHE_ADD`` — gates the cache-write branch in
        ``get_reference_model``: ``if cache_add() and cache_path:``. Default is
        unset (falsy), so writes never happen unless we explicitly enable them.

    Without both, repeat calls for the same accession still pay ``parser.parse``
    on the full reference content every request — ~20 s for a chromosome-scale
    accession like ``NC_000016.10`` (chr16 GFF3 + FASTA).

    The cache directory is created at startup so the retriever can write into it.
    """
    from mutalyzer_retriever.configuration import settings

    cache_dir = Path(os.environ.get("MUTALYZER_CACHE_DIR", "/data/mutalyzer/cache"))
    cache_dir.mkdir(parents=True, exist_ok=True)
    settings["MUTALYZER_CACHE_DIR"] = str(cache_dir)
    settings["MUTALYZER_FILE_CACHE_ADD"] = True


_configure_retriever_cache()

# Imports below this line; they don't trigger any retrieval at module load.
from mutalyzer.back_translator import back_translate as _mt_back_translate  # noqa: E402
from mutalyzer.normalizer import normalize as _mt_normalize  # noqa: E402


class MutalyzerError(Exception):
    """Mutalyzer returned an error response."""

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


def normalize_raw(hgvs: str) -> dict[str, Any]:
    """Return Mutalyzer's raw normalize response — including any error entries.

    Used by the ``/mutalyzer/normalize`` passthrough endpoint, which mirrors
    mutalyzer.nl's public API shape.
    """
    if _FS_PATTERN.search(hgvs.split(":", 1)[-1]):
        return _normalize_frameshift(hgvs)
    return cast("dict[str, Any]", _mt_normalize(hgvs))


def normalize(hgvs: str) -> dict[str, Any]:
    """Return a trimmed normalize response; raise :class:`MutalyzerError` on failure.

    Used by the Phase 6 pipeline orchestrator.
    """
    response = normalize_raw(hgvs)
    error = _extract_error(response)
    if error:
        raise MutalyzerError(code=error[0], message=error[1])
    return _trim(response)


def back_translate(hgvsp: str) -> list[str]:
    """Back-translate a protein HGVS description to coding-variant alternatives."""
    if _FS_PATTERN.search(hgvsp.split(":", 1)[-1]):
        raise MutalyzerError(
            "FRAMESHIFT_UNSUPPORTED",
            "back-translation of frameshift variants not supported",
        )
    return list(_mt_back_translate(hgvsp))
