#!/usr/bin/env bash
# One-shot bootstrap + per-dataset refresh for variant-lookup.
#
# Subcommands:
#   ./scripts/setup.sh                  # full bootstrap (everything in order)
#   ./scripts/setup.sh vendor-vv        # clone rest_variantValidator at origin/master
#   ./scripts/setup.sh ensure-vv-dirs   # create the bind-mount targets under ${DATA_DIR}/variantvalidator/
#   ./scripts/setup.sh build-vv         # build VariantValidator's docker images (slow, ~1 h)
#   ./scripts/setup.sh refresh-echtvar  # download gnomAD VCFs and encode the echtvar archive
#   ./scripts/setup.sh refresh-refseq   # rebuild the RefSeq MANE-Select index
#   ./scripts/setup.sh refresh-mutalyzer-cache  # pre-populate the Mutalyzer chromosome cache
#   ./scripts/setup.sh build-gateway    # build the gateway image
#   ./scripts/setup.sh cleanup-echtvar-staging  # delete VCF staging dir after successful encode
#
# Reads .env for DATA_DIR and pinned SHAs. All steps are idempotent.
# Host requirements: bash, git, curl, docker (+ docker compose).
# echtvar runs only inside the gateway image via `docker run`, so the host
# does not need echtvar (or any other bioinformatics tooling) installed.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${REPO_ROOT}/.env"
[ -f "${ENV_FILE}" ] || { echo "ERROR: ${ENV_FILE} missing — copy .env.example first." >&2; exit 1; }
set -a; . "${ENV_FILE}"; set +a

: "${DATA_DIR:?DATA_DIR must be set in .env}"
: "${AWS_CLI_VERSION:=2.34.30}"

VENDOR_DIR="${REPO_ROOT}/vendor"
IMAGE_TAG="variant-lookup-gateway:latest"

log() { printf '==> %s\n' "$*"; }

ensure_vv_data_dirs() {
    # Bind-mount targets referenced by compose.vv-override.yml. Must exist
    # before `docker compose up` so the bind mounts resolve.
    log "Ensuring VariantValidator data directories under ${DATA_DIR}/variantvalidator/"
    mkdir -p \
        "${DATA_DIR}/variantvalidator/seqdata" \
        "${DATA_DIR}/variantvalidator/logs" \
        "${DATA_DIR}/variantvalidator/vdb-mysql" \
        "${DATA_DIR}/variantvalidator/vvta-postgres"
}

require_gateway_image() {
    if ! docker image inspect "${IMAGE_TAG}" >/dev/null 2>&1; then
        echo "ERROR: ${IMAGE_TAG} not built — run '$0 build-gateway' first." >&2
        exit 1
    fi
}

vendor_vv() {
    # We track upstream master because we have to, not because we want to.
    # rest_variantValidator's pyproject.toml pins its three sub-libraries to
    # `@master`, so pip installs whatever's on master HEAD at image build
    # time regardless of what we'd pin here. The only way to lock the stack
    # transitively would be to patch upstream's pyproject.toml — which is a
    # modification that triggers AGPL §13. So we accept the float.
    # See ARCHITECTURE.md § "AGPL boundary" — replacing VV with a permissively
    # licensed alternative is the longer-term plan.
    log "Vendoring rest_variantValidator (AGPL-3.0-only) at master HEAD"
    mkdir -p "${VENDOR_DIR}"
    local dir="${VENDOR_DIR}/rest_variantValidator"
    if [ -d "${dir}/.git" ]; then
        git -C "${dir}" fetch --quiet origin master
    else
        git clone --quiet "https://github.com/openvar/rest_variantValidator" "${dir}"
    fi
    git -C "${dir}" checkout --quiet --detach origin/master
    local sha
    sha="$(git -C "${dir}" rev-parse HEAD)"
    log "    rest_variantValidator @ ${sha}"
}

build_vv() {
    [ -d "${VENDOR_DIR}/rest_variantValidator" ] \
        || { echo "ERROR: run vendor-vv first." >&2; exit 1; }
    log "Building VariantValidator images (first build ~1 h)"
    ( cd "${VENDOR_DIR}/rest_variantValidator" && docker compose build )
}

refresh_echtvar() {
    require_gateway_image
    local final_dir="${DATA_DIR}/echtvar"
    local all_present=1
    for chr in {1..22} X Y; do
        if [ ! -s "${final_dir}/gnomad.joint.v4.1.chr${chr}.echtvar.zip" ]; then
            all_present=0
            break
        fi
    done
    if [ "${all_present}" -eq 1 ]; then
        log "Found all 24 per-chromosome archives under ${final_dir}; nothing to do"
        log "    delete one (or all) to force a rebuild"
        return 0
    fi

    local stage="${DATA_DIR}/echtvar/staging"
    local build="${DATA_DIR}/echtvar/build"
    mkdir -p "${stage}" "${build}" "${final_dir}"

    # Override the AWS CLI's default 10-concurrent / 8 MB chunk-size — too
    # conservative across the cross-Pacific link. There's no env-var path
    # for these s3 tuning settings; they have to live in a config file.
    cat > "${stage}/.aws-config" <<'AWS_CONFIG'
[default]
s3 =
    max_concurrent_requests = 50
    max_queue_size = 10000
    multipart_chunksize = 16MB
    multipart_threshold = 16MB
AWS_CONFIG

    log "Syncing gnomAD v4.1 joint VCFs from S3 to ${stage}"
    # Public bucket, so --no-sign-request avoids needing AWS credentials.
    # HOME=/tmp because we run as the host UID, which has no entry in the
    # container's /etc/passwd and so no home dir the CLI could write into.
    docker run --rm \
        --user "$(id -u):$(id -g)" \
        -e HOME=/tmp \
        -e AWS_CONFIG_FILE=/stage/.aws-config \
        -v "${stage}:/stage" \
        "amazon/aws-cli:${AWS_CLI_VERSION}" \
        s3 sync s3://gnomad-public-us-east-1/release/4.1/vcf/joint/ /stage/ \
            --no-sign-request \
            --exclude "*" \
            --include "gnomad.joint.v4.1.sites.chr*.vcf.bgz"

    log "Writing echtvar field config"
    cat > "${build}/gnomad.v4.1.joint.json" <<'JSON'
[
    {"field": "AC_joint", "alias": "gnomad_ac"},
    {"field": "AN_joint", "alias": "gnomad_an"},
    {"field": "nhomalt_joint", "alias": "gnomad_nhomalt"},
    {"field": "AC_joint_XY", "alias": "gnomad_ac_xy"},
    {"field": "fafmax_faf95_max_joint", "alias": "gnomad_faf95_max", "multiplier": 2000000},
    {"field": "fafmax_faf95_max_gen_anc_joint", "alias": "gnomad_faf95_max_gen_anc"}
]
JSON

    # echtvar encode is single-threaded per VCF (vcf.set_threads(2) only adds
    # two bgzf-decompression helpers), so a one-shot invocation with all 24
    # VCFs leaves N-1 host cores idle. The output zip's paths are
    # `echtvar/<chrom>/<block>/...` — disjoint across chromosomes — so we
    # encode each chromosome in its own container in parallel, and the gateway
    # dispatches lookups per-chrom against the matching archive. There is no
    # merge step: categorical INFO field strings tables are built in insertion
    # order per VCF, so per-chrom tables don't share indices, and a single
    # merged archive would need a stream-vbyte-aware remap to be correct.
    log "Encoding 24 per-chromosome echtvar archives in parallel via ${IMAGE_TAG}"
    log "    per-chr logs at ${build}/echtvar-encode-chr*.log"
    local pids=() chr_archive
    for chr in {1..22} X Y; do
        chr_archive="${final_dir}/gnomad.joint.v4.1.chr${chr}.echtvar.zip"
        if [ -s "${chr_archive}" ]; then
            log "    chr${chr}: ${chr_archive} present, skipping encode (delete to force re-encode)"
            continue
        fi
        docker run --rm \
            --user "$(id -u):$(id -g)" \
            -v "${stage}:/stage:ro" \
            -v "${build}:/build" \
            --workdir /build \
            --name "echtvar-encode-chr${chr}" \
            "${IMAGE_TAG}" \
            echtvar encode \
                "gnomad.joint.v4.1.chr${chr}.echtvar.zip" \
                gnomad.v4.1.joint.json \
                "/stage/gnomad.joint.v4.1.sites.chr${chr}.vcf.bgz" \
            > "${build}/echtvar-encode-chr${chr}.log" 2>&1 \
            &
        pids+=("$!")
    done
    local rc=0
    for pid in "${pids[@]}"; do
        wait "${pid}" || rc=$?
    done
    if [ "${rc}" -ne 0 ]; then
        echo "ERROR: at least one echtvar encode failed; see ${build}/echtvar-encode-chr*.log" >&2
        exit "${rc}"
    fi

    log "Moving per-chromosome archives into ${final_dir}/"
    for chr in {1..22} X Y; do
        chr_archive="${build}/gnomad.joint.v4.1.chr${chr}.echtvar.zip"
        if [ -s "${chr_archive}" ]; then
            mv -f "${chr_archive}" "${final_dir}/"
        fi
    done
    log "Wrote 24 per-chromosome archives to ${final_dir}/"
}

refresh_refseq() {
    require_gateway_image
    log "Building RefSeq MANE-Select / Select index via ${IMAGE_TAG}"
    mkdir -p "${DATA_DIR}/refseq"
    docker run --rm \
        --user "$(id -u):$(id -g)" \
        -v "${DATA_DIR}/refseq:/data/refseq" \
        "${IMAGE_TAG}" \
        python -m variant_lookup.refseq_build /data/refseq/refseq_processed.json
    log "Wrote ${DATA_DIR}/refseq/refseq_processed.json"
}

build_gateway() {
    log "Building gateway image"
    ( cd "${REPO_ROOT}" && docker compose build gateway )
}

refresh_mutalyzer_cache() {
    # Pre-populate mutalyzer-retriever's file cache with every GRCh37/38
    # chromosomal NC_ reference. Upstream's documented populator:
    # https://mutalyzer.readthedocs.io/en/latest/usage.html#enable-the-file-based-cache
    #
    # Without this, the gateway's first request for each chromosomal
    # reference pays a ~20 s NCBI fetch + parse, AND upstream's @lru_cache
    # on the file-cache readers poisons the in-process cache with the
    # cold-miss None — so repeat requests for the same accession in the
    # same gateway process keep paying the full parse cost. See
    # src/variant_lookup/mutalyzer_populate.py for details.
    require_gateway_image
    : "${NCBI_EUTILS_EMAIL:?NCBI_EUTILS_EMAIL must be set in .env}"

    log "Pre-populating Mutalyzer cache for GRCh37+GRCh38 chromosomal NC_ refs"
    log "    target: docker volume variant-lookup_mutalyzer-cache"
    log "    expect ~30-60 min depending on NCBI throughput and API-key tier"
    docker run --rm \
        -v variant-lookup_mutalyzer-cache:/data/mutalyzer \
        -e NCBI_EUTILS_EMAIL="${NCBI_EUTILS_EMAIL}" \
        -e NCBI_EUTILS_API_KEY="${NCBI_EUTILS_API_KEY:-}" \
        "${IMAGE_TAG}" \
        /app/.venv/bin/python -m variant_lookup.mutalyzer_populate
    log "Done. Mutalyzer cache pre-populated under variant-lookup_mutalyzer-cache"
}

cleanup_echtvar_staging() {
    # Once all 24 per-chromosome archives exist in ${DATA_DIR}/echtvar/, the
    # source VCFs (~700 GB) and the build/ intermediate dir can go.
    local final_dir="${DATA_DIR}/echtvar"
    local stage="${DATA_DIR}/echtvar/staging"
    local build="${DATA_DIR}/echtvar/build"
    local missing=()
    for chr in {1..22} X Y; do
        if [ ! -s "${final_dir}/gnomad.joint.v4.1.chr${chr}.echtvar.zip" ]; then
            missing+=("chr${chr}")
        fi
    done
    if [ "${#missing[@]}" -gt 0 ]; then
        echo "ERROR: per-chr archives missing or empty in ${final_dir}: ${missing[*]}" >&2
        echo "       refusing to delete staging." >&2
        exit 1
    fi
    log "Removing staging VCFs at ${stage} and ${build}"
    rm -rf "${stage}" "${build}"
    log "Done. 24 per-chromosome archives preserved under ${final_dir}/"
}

bootstrap() {
    vendor_vv
    ensure_vv_data_dirs
    build_vv
    build_gateway      # must precede refresh_echtvar + refresh_mutalyzer_cache
    refresh_echtvar
    refresh_refseq
    refresh_mutalyzer_cache
    cat <<EOF

Bootstrap complete. Bring the stack up with:

  docker compose \\
    -f docker-compose.yml \\
    -f vendor/rest_variantValidator/docker-compose.yml \\
    -f compose.vv-override.yml \\
    up -d

EOF
}

case "${1:-bootstrap}" in
    bootstrap)                  bootstrap ;;
    vendor-vv)                  vendor_vv ;;
    ensure-vv-dirs)             ensure_vv_data_dirs ;;
    build-vv)                   build_vv ;;
    refresh-echtvar)            refresh_echtvar ;;
    refresh-refseq)             refresh_refseq ;;
    refresh-mutalyzer-cache)    refresh_mutalyzer_cache ;;
    build-gateway)              build_gateway ;;
    cleanup-echtvar-staging)    cleanup_echtvar_staging ;;
    -h|--help|help)
        sed -n '2,15p' "$0"
        ;;
    *)
        echo "Unknown subcommand: $1" >&2
        echo "Run '$0 --help' for usage." >&2
        exit 2
        ;;
esac
