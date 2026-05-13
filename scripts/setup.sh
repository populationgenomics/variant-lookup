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
#   ./scripts/setup.sh build-gateway    # build the gateway image
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
    local stage="${DATA_DIR}/echtvar/staging"
    local build="${DATA_DIR}/echtvar/build"
    mkdir -p "${stage}" "${build}"

    log "Downloading gnomAD v4.1 joint VCFs to ${stage}"
    for chr in {1..22} X Y; do
        local f="gnomad.joint.v4.1.sites.chr${chr}.vcf.bgz"
        if [ -f "${stage}/${f}" ]; then
            printf '    %s present, skipping\n' "${f}"
        else
            curl -fsSL --output "${stage}/${f}" \
                "https://gnomad-public-us-east-1.s3.amazonaws.com/release/4.1/vcf/joint/${f}"
        fi
    done

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

    log "Encoding echtvar archive via ${IMAGE_TAG} (~1-2 h)"
    local vcf_args=()
    for chr in {1..22} X Y; do
        vcf_args+=("/stage/gnomad.joint.v4.1.sites.chr${chr}.vcf.bgz")
    done
    docker run --rm \
        --user "$(id -u):$(id -g)" \
        -v "${stage}:/stage:ro" \
        -v "${build}:/build" \
        --workdir /build \
        "${IMAGE_TAG}" \
        echtvar encode \
            gnomad.joint.v4.1.echtvar.zip \
            gnomad.v4.1.joint.json \
            "${vcf_args[@]}"

    mv -f "${build}/gnomad.joint.v4.1.echtvar.zip" "${DATA_DIR}/echtvar/"
    log "Wrote ${DATA_DIR}/echtvar/gnomad.joint.v4.1.echtvar.zip"
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

bootstrap() {
    vendor_vv
    ensure_vv_data_dirs
    build_vv
    build_gateway      # must precede refresh_echtvar (encode runs inside this image)
    refresh_echtvar
    refresh_refseq
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
    bootstrap)        bootstrap ;;
    vendor-vv)        vendor_vv ;;
    ensure-vv-dirs)   ensure_vv_data_dirs ;;
    build-vv)         build_vv ;;
    refresh-echtvar)  refresh_echtvar ;;
    refresh-refseq)   refresh_refseq ;;
    build-gateway)    build_gateway ;;
    -h|--help|help)
        sed -n '2,15p' "$0"
        ;;
    *)
        echo "Unknown subcommand: $1" >&2
        echo "Run '$0 --help' for usage." >&2
        exit 2
        ;;
esac
