"""WSGI entry point for mutalyzer-api with the file-cache None-poison fix.

Two things this module does, both at import time before the Flask app loads:

1. Replace ``mutalyzer_retriever.retriever.get_annotations_from_file_cache``
   and ``get_sequence_from_file_cache`` with bounded LRU wrappers that
   **do not cache None results**.

   Upstream decorates these with ``@lru_cache(maxsize=N)``. The lru_cache
   faithfully caches the ``None`` returned on a cold-cache miss, so even
   after ``retrieve_model`` later writes the file, subsequent calls in the
   same worker keep returning the cached ``None`` and re-running the full
   NCBI fetch + GFF3/FASTA reparse (~hundreds of ms to seconds per
   accession). Repeats stay slow until the worker is restarted. The
   wrapper caches only non-None returns so a later call lands on the now-
   present file.

2. Bound memory.

   Each entry holds a parsed annotations JSON (~15 MB for a chromosome)
   or raw sequence text (~86 MB for a chromosome) or raw fetched bytes
   (similar magnitudes — that one is upstream's own lru_cache on
   ``retrieve_raw`` and is bounded via the ``MUTALYZER_LRU_CACHE_MAXSIZE``
   setting the entrypoint writes into mutalyzer-retriever's config file).
   The two file-cache LRUs here use the same cap to keep memory growth
   predictable across workers.

If upstream ever fixes the None-poison behaviour, drop this module and
switch gunicorn back to ``mutalyzer_api.endpoints:app``.
"""

import functools
import os
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

import mutalyzer_retriever.retriever as _mr

_MAXSIZE = int(os.environ.get("MUTALYZER_LRU_CACHE_MAXSIZE", "25"))


def _bounded_non_none_lru(maxsize: int, fn: Callable[[str], Any]) -> Callable[[str], Any]:
    """Memoise on r_id with an LRU `maxsize` cap. Never caches a ``None``."""
    cache: OrderedDict[str, Any] = OrderedDict()

    @functools.wraps(fn)
    def wrapper(r_id: str) -> Any:
        if r_id in cache:
            cache.move_to_end(r_id)
            return cache[r_id]
        result = fn(r_id)
        if result is not None:
            cache[r_id] = result
            if len(cache) > maxsize:
                cache.popitem(last=False)
        return result

    return wrapper


# ``__wrapped__`` unwraps the upstream @lru_cache to get the original function.
_mr.get_annotations_from_file_cache = _bounded_non_none_lru(
    _MAXSIZE, _mr.get_annotations_from_file_cache.__wrapped__
)
_mr.get_sequence_from_file_cache = _bounded_non_none_lru(
    _MAXSIZE, _mr.get_sequence_from_file_cache.__wrapped__
)


# Import the Flask app *after* the patch so any module that does
# `from mutalyzer_retriever.retriever import get_annotations_from_file_cache`
# at its own import time binds to the wrapped version.
from mutalyzer_api.endpoints import app  # noqa: E402

__all__ = ["app"]
