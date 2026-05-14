#!/bin/sh
# Generate the mutalyzer-retriever config file from env vars and launch
# gunicorn. mutalyzer_retriever.configuration reads MUTALYZER_SETTINGS at
# import time, expecting a file path; envvar-only configuration is not
# supported upstream, so we materialise the file here.

set -eu

: "${NCBI_EUTILS_EMAIL:?NCBI_EUTILS_EMAIL must be set (NCBI requires caller identification)}"

mkdir -p "${MUTALYZER_CACHE_DIR}"

CONFIG_PATH=/etc/mutalyzer.conf
{
    printf "MUTALYZER_CACHE_DIR = %s\n" "${MUTALYZER_CACHE_DIR}"
    printf "MUTALYZER_FILE_CACHE_ADD = true\n"
    printf "EMAIL = %s\n" "${NCBI_EUTILS_EMAIL}"
    if [ -n "${NCBI_EUTILS_API_KEY:-}" ]; then
        printf "NCBI_API_KEY = %s\n" "${NCBI_EUTILS_API_KEY}"
    fi
} > "${CONFIG_PATH}"
export MUTALYZER_SETTINGS="${CONFIG_PATH}"

# If the first argument is the populator marker, run the cache populator
# instead of the server. Used by scripts/setup.sh refresh-mutalyzer-cache so
# the populator runs in the same image as the service (consistent deps + config).
if [ "${1:-}" = "populate-cache" ]; then
    exec python -c "
import sys
from mutalyzer_retriever.cli import main
sys.argv = [
    'mutalyzer_retriever', 'ncbi_assemblies',
    '--ref_id_start', 'NC_',
    '--assembly_id_start', 'GRCh',
    '--output', '${MUTALYZER_CACHE_DIR}',
    '--include_sequence',
]
main()
"
fi

exec gunicorn \
    "mutalyzer_api.endpoints:app" \
    --workers "${MUTALYZER_API_WORKERS}" \
    --bind "0.0.0.0:${MUTALYZER_API_PORT}" \
    --timeout 120 \
    --access-logfile - \
    --error-logfile -
