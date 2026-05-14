#!/bin/sh
# Generate the mutalyzer-retriever config file from env vars and launch
# gunicorn against our wsgi.py wrapper (see ../wsgi.py for the file-cache
# None-poison fix and bounded LRU).
#
# mutalyzer_retriever.configuration reads MUTALYZER_SETTINGS at import time,
# expecting a file path; envvar-only configuration is not supported upstream,
# so we materialise the file here.

set -eu

: "${NCBI_EUTILS_EMAIL:?NCBI_EUTILS_EMAIL must be set (NCBI requires caller identification)}"

mkdir -p "${MUTALYZER_CACHE_DIR}"

CONFIG_PATH=/etc/mutalyzer.conf
{
    printf "MUTALYZER_CACHE_DIR = %s\n" "${MUTALYZER_CACHE_DIR}"
    printf "MUTALYZER_FILE_CACHE_ADD = true\n"
    # Bounds upstream's @lru_cache on retrieve_raw (and a couple of related
    # functions). wsgi.py monkey-patches the two file-cache readers with its
    # own bounded LRU keyed on the same env var, so all three caches respect
    # the same cap.
    printf "MUTALYZER_LRU_CACHE_MAXSIZE = %s\n" "${MUTALYZER_LRU_CACHE_MAXSIZE}"
    printf "EMAIL = %s\n" "${NCBI_EUTILS_EMAIL}"
    if [ -n "${NCBI_EUTILS_API_KEY:-}" ]; then
        printf "NCBI_API_KEY = %s\n" "${NCBI_EUTILS_API_KEY}"
    fi
} > "${CONFIG_PATH}"
export MUTALYZER_SETTINGS="${CONFIG_PATH}"

exec gunicorn \
    "wsgi:app" \
    --chdir /app \
    --workers "${MUTALYZER_API_WORKERS}" \
    --bind "0.0.0.0:${MUTALYZER_API_PORT}" \
    --timeout 120 \
    --access-logfile - \
    --error-logfile -
