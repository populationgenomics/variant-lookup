# Multi-stage: uv-managed dependency install → minimal runtime image.

FROM python:3.13-slim AS builder

# `description-extractor` (transitive via `mutalyzer`) has no published wheel.
# Its sdist's setup.py shells out to `git clone https://github.com/mutalyzer/
# extractor-core.git` from a custom build_ext, then compiles a C++17 extension
# against those sources. python:3.13-slim has neither git nor a C++ toolchain,
# so we install them here. The builder stage is discarded — none of this ends
# up in the runtime image.
# Caveat: this also means each build pulls `master` HEAD of extractor-core;
# the build is non-reproducible by upstream's design and needs network egress
# to github.com.
RUN set -eu \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
       git ca-certificates build-essential \
    && rm -rf /var/lib/apt/lists/*

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

# Run as non-root. Builder-stage files in /app are world-readable + world-
# executable from the default umask, so a non-privileged user can read the
# venv + source and exec python. The only writable mount in the gateway is
# /data/mutalyzer (a docker-managed named volume in our compose, so docker
# initialises ownership to this UID on first mount).
RUN groupadd --system --gid 1000 app \
    && useradd --system --uid 1000 --gid 1000 --no-create-home --shell /usr/sbin/nologin app
USER 1000:1000

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/src" \
    PYTHONUNBUFFERED=1

EXPOSE 8000

# Invoke uvicorn through the venv's python directly rather than via the
# uvicorn entry-point script. uv writes absolute shebangs (e.g.
# `#!/build/.venv/bin/python`) that reference the builder stage's path —
# after COPY into /app/.venv the shebang target no longer exists in the
# runtime image and exec fails with "no such file or directory". /app/.venv
# /bin/python is a symlink to /usr/local/bin/python3 which exists, and
# Python detects the venv from the adjacent pyvenv.cfg so site-packages
# is the venv's.
CMD ["/app/.venv/bin/python", "-m", "uvicorn", "variant_lookup.main:app", "--host", "0.0.0.0", "--port", "8000"]
