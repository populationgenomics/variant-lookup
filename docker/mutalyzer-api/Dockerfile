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
# files (<r_id>.annotations + <r_id>.sequence) are written lazily on first
# request per accession and persist across restarts. wsgi.py applies a
# monkey-patch to fix upstream's None-poison in the file-cache readers.

FROM python:3.13-slim AS builder

# description-extractor (transitive via mutalyzer) has no published wheel.
# Its sdist's setup.py shells out to `git clone https://github.com/mutalyzer/
# extractor-core.git` and compiles a C++17 extension against those sources.
# We need git + a C++ toolchain here in the builder; the runtime image
# discards both.
RUN set -eu \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        git ca-certificates build-essential \
    && rm -rf /var/lib/apt/lists/*

ARG MUTALYZER_API_VERSION=0.3.2
ARG GUNICORN_VERSION=23.0.0

RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
# `setuptools<81` is required at runtime: mutalyzer_api's __init__.py does
# `from pkg_resources import get_distribution`, and setuptools 81+ removed
# the pkg_resources shim. Python 3.13's venv doesn't ship setuptools by
# default, so we have to install a compatible version explicitly.
RUN pip install --no-cache-dir \
        "mutalyzer-api==${MUTALYZER_API_VERSION}" \
        "gunicorn==${GUNICORN_VERSION}" \
        "setuptools<81"


FROM python:3.13-slim AS runtime

COPY --from=builder /opt/venv /opt/venv

ENV PATH=/opt/venv/bin:$PATH \
    MUTALYZER_CACHE_DIR=/data/mutalyzer/cache \
    MUTALYZER_API_WORKERS=4 \
    MUTALYZER_API_PORT=5000 \
    MUTALYZER_LRU_CACHE_MAXSIZE=25

WORKDIR /app
COPY wsgi.py /app/wsgi.py
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 5000
ENTRYPOINT ["/entrypoint.sh"]
