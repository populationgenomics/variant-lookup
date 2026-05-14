# Mutalyzer 3 HTTP API service.
#
# Wraps the MIT-licensed mutalyzer + mutalyzer-api packages behind gunicorn.
# Sits at the same architectural layer as the VariantValidator REST service:
# the gateway speaks to both over HTTP. Mutalyzer is MIT so the boundary is
# not license-driven (as VV's AGPL boundary is); we cross it for operational
# clarity, replaceability, and to isolate mutalyzer-retriever's per-process
# reference-sequence LRU cache.
#
# Cache: bind-mount ${DATA_DIR}/mutalyzer/cache into /data/mutalyzer/cache;
# the on-disk format is mutalyzer-retriever's <r_id>.annotations +
# <r_id>.sequence pair, populated by `scripts/setup.sh refresh-mutalyzer-cache`.
# Each gunicorn worker maintains its own in-process LRU on top of that.

FROM python:3.13-slim

ARG MUTALYZER_API_VERSION=0.3.2
ARG GUNICORN_VERSION=23.0.0

RUN pip install --no-cache-dir \
        "mutalyzer-api==${MUTALYZER_API_VERSION}" \
        "gunicorn==${GUNICORN_VERSION}"

ENV MUTALYZER_CACHE_DIR=/data/mutalyzer/cache \
    MUTALYZER_API_WORKERS=4 \
    MUTALYZER_API_PORT=5000

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 5000
ENTRYPOINT ["/entrypoint.sh"]
