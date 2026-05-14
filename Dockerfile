# Multi-stage: uv-managed dependency install → minimal runtime image.

FROM python:3.13-slim AS builder

RUN pip install --no-cache-dir uv

WORKDIR /build
COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/

# uv.lock is required — run `uv lock` locally and commit it before building.
RUN uv sync --frozen --no-dev


FROM python:3.13-slim AS runtime

# echtvar — subprocessed by the gateway for gnomAD frequency lookups.
# Precompiled binary, pinned. Override ECHTVAR_VERSION via build-arg when bumping.
ARG ECHTVAR_VERSION=v0.2.4
ARG TARGETARCH
RUN set -eu \
    && apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && case "${TARGETARCH}" in \
         amd64) ECHTVAR_TARGET=x86_64-unknown-linux-musl ;; \
         arm64) ECHTVAR_TARGET=aarch64-unknown-linux-musl ;; \
         *) echo "Unsupported TARGETARCH: ${TARGETARCH}" >&2; exit 1 ;; \
       esac \
    && curl -fsSL \
       "https://github.com/brentp/echtvar/releases/download/${ECHTVAR_VERSION}/echtvar-${ECHTVAR_VERSION}-${ECHTVAR_TARGET}" \
       -o /usr/local/bin/echtvar \
    && chmod +x /usr/local/bin/echtvar \
    && apt-get purge -y --auto-remove curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=builder /build/.venv /app/.venv
COPY --from=builder /build/src /app/src

# We deliberately leave USER unset (i.e. root). All reference data under /data
# is bind-mounted from the host (echtvar archives, refseq index, mutalyzer
# cache). Matching the host UID inside the container would require
# per-deployment .env wiring; root reads any UID/GID via the bind mount, so we
# sidestep that. Writes to /data/mutalyzer/cache only happen during cache
# population — the runtime gateway holds no writable bind mounts.

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/src" \
    PYTHONUNBUFFERED=1 \
    UVICORN_WORKERS=4

EXPOSE 8000

# Invoke uvicorn through the venv's python directly rather than via the
# uvicorn entry-point script. uv writes absolute shebangs (e.g.
# `#!/build/.venv/bin/python`) that reference the builder stage's path —
# after COPY into /app/.venv the shebang target no longer exists in the
# runtime image and exec fails with "no such file or directory". /app/.venv
# /bin/python is a symlink to /usr/local/bin/python3 which exists, and
# Python detects the venv from the adjacent pyvenv.cfg so site-packages
# is the venv's.
#
# Wrapped in `sh -c` so $UVICORN_WORKERS expands at container start, letting
# operators override via compose env without rebuilding the image.
CMD ["sh", "-c", "exec /app/.venv/bin/python -m uvicorn variant_lookup.main:app --host 0.0.0.0 --port 8000 --workers ${UVICORN_WORKERS:-4}"]
