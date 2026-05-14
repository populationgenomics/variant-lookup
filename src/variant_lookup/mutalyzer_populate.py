"""Pre-populate the mutalyzer-retriever file cache with GRCh37/38 chromosomal refs.

Wrapper around upstream's documented ``mutalyzer_retriever ncbi_assemblies``
populator (see https://mutalyzer.readthedocs.io/en/latest/usage.html#enable-the-file-based-cache).
We can't invoke the CLI script directly because the installed entry point has
a broken shebang (uv writes ``#!/build/.venv/bin/python``; cf. Dockerfile
comment on the uvicorn invocation). Instead, we patch ``sys.argv`` and call
the CLI's ``main`` from Python.

Why pre-populate at all: mutalyzer-retriever's runtime path on a cold cache
fetches the full reference (GFF3 + FASTA) from NCBI and runs ``parser.parse``
over it. For chromosome-scale ``NC_*`` accessions that's ~20 s per first
request (chr16 GFF3 is ~16 MB, FASTA ~87 MB). Worse, the upstream
``@lru_cache`` on ``get_*_from_file_cache`` poisons the in-process cache with
the negative result from the cold-miss check, so even after the file is
written subsequent requests in the same process miss the file cache and pay
the full parse cost again. Pre-populating avoids both issues: the cache
files exist from day one, so the very first request reads them from disk.

Non-chromosomal references (``NM_*``, ``NP_*``) are smaller (~10-100 kB) and
warm into the cache on demand. They still hit the upstream LRU-poison bug
but the per-request cost is bounded.

Invoked as a docker run via ``scripts/setup.sh refresh-mutalyzer-cache``:

    docker run --rm \\
        -v variant-lookup_mutalyzer-cache:/data/mutalyzer \\
        -e NCBI_EUTILS_EMAIL=... \\
        -e NCBI_EUTILS_API_KEY=... \\
        variant-lookup-gateway:latest \\
        /app/.venv/bin/python -m variant_lookup.mutalyzer_populate
"""

import os
import sys
from pathlib import Path

# Step 1: patch mutalyzer-retriever's settings dict BEFORE any submodule that
# reads it at import time. sources/ncbi.py copies EMAIL/NCBI_API_KEY onto
# Bio.Entrez at module-import time, so the patch has to happen before that.
from mutalyzer_retriever.configuration import settings

try:
    settings["EMAIL"] = os.environ["NCBI_EUTILS_EMAIL"]
except KeyError as e:
    raise SystemExit("NCBI_EUTILS_EMAIL must be set (NCBI requires caller identification)") from e

if api_key := os.environ.get("NCBI_EUTILS_API_KEY"):
    settings["NCBI_API_KEY"] = api_key

# Step 2: ensure the cache directory exists, then invoke the CLI populator.
# The cli import is deferred (E402) on purpose — it transitively triggers
# sources/ncbi.py which reads settings at import time; see step 1 above.
output_dir = Path("/data/mutalyzer/cache")
output_dir.mkdir(parents=True, exist_ok=True)

from mutalyzer_retriever.cli import main  # noqa: E402

sys.argv = [
    "mutalyzer_retriever",
    "ncbi_assemblies",
    "--ref_id_start",
    "NC_",
    "--assembly_id_start",
    "GRCh",
    "--output",
    str(output_dir),
    "--include_sequence",
]
main()
