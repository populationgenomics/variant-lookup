# variant-lookup

Self-hosted REST service for variant normalization and gnomAD v4 frequency lookups.

Replaces the chain of rate-limited / unreliable external services (Mutalyzer, VariantValidator, gnomAD GraphQL) typically used to turn messy LLM-extracted variant strings into normalized HGVS descriptions, GRCh38 pseudo-VCFs, and population frequencies.

See **[ARCHITECTURE.md](ARCHITECTURE.md)** for the system design, the public API contract, the configuration surface, and the AGPL boundary that future contributors must respect.

## Local development

Requires Python 3.13, a C++17 compiler, `git`, and [uv](https://docs.astral.sh/uv/). `description-extractor`'s sdist (a transitive Mutalyzer dep) clones a sibling repo at build time and compiles a C++17 extension — `gcc 7+` / `clang 5+` is enough.

```bash
uv sync
uv run pytest                                          # ~95 tests, ~1 s wall
uv run pre-commit run --all-files                      # ruff + ruff-format + mypy strict
uv run uvicorn variant_lookup.main:app --port 8000     # /readyz will be degraded — no real upstreams
```

## Production setup

Tested on Linux with Docker 20.10+ and Compose v2. Host needs `bash`, `git`, `curl`, `docker` — no Python or bioinformatics tooling, every reference-data step runs inside the gateway image.

### 1. Configure

```bash
git clone https://github.com/populationgenomics/variant-lookup
cd variant-lookup
cp .env.example .env
$EDITOR .env
```

Fill in at least `DATA_DIR` (host path with ≥1 TB free for the transient gnomAD download), `SSL_CERT_FILE` + `SSL_KEY_FILE` (absolute paths to your server cert chain and private key — files are bind-mounted individually, so any host filenames work), `API_KEYS_HOST_FILE` (we'll create this in step 3), and `NCBI_EUTILS_EMAIL`. `NCBI_EUTILS_API_KEY` is optional (raises NCBI's rate limit 3→10 req/s).

### 2. Bootstrap

```bash
./scripts/setup.sh bootstrap
```

This runs, in order:

- `vendor-vv` — clone `openvar/rest_variantValidator` at master HEAD into `vendor/`
- `ensure-vv-dirs` — create the bind-mount targets under `${DATA_DIR}/variantvalidator/`
- `build-vv` — `docker compose build` the upstream VV stack (~1 h first time, then cached)
- `build-gateway` — `docker compose build` the gateway image (fast)
- `refresh-echtvar` — `aws s3 sync` of gnomAD v4.1 joint VCFs (~800 GB) from the public bucket, then parallel `echtvar encode` per chromosome (~3-5 h total)
- `refresh-refseq` — build the MANE-Select / RefSeq-Select index from NCBI RefSeq's GFF (minutes)

Each subcommand is idempotent and resumable. Once `refresh-echtvar` produces all 24 archives:

```bash
./scripts/setup.sh cleanup-echtvar-staging       # reclaims ~800 GB of source VCFs
```

### 3. Generate an API key

The CLI runs inside the gateway image so the host needs no Python or argon2-cffi:

```bash
mkdir -p ~/variant-lookup-secrets
docker run --rm \
    --user "$(id -u):$(id -g)" \
    -v "$HOME/variant-lookup-secrets:/work" \
    variant-lookup-gateway:latest \
    python -m variant_lookup.manage_keys --file /work/api-keys.yaml add <name>
```

The bearer token (`<name>.<32-byte-hex>`) is printed once — save it. Set `API_KEYS_HOST_FILE` in `.env` to the absolute path of the resulting YAML.

### 4. Bring up the stack

Three compose files — ours, the vendored VV stack, and an override that redirects VV's bind mounts away from `$HOME`, adds DB persistence, and drops host port publishes that the gateway doesn't need:

```bash
docker compose \
    -f docker-compose.yml \
    -f vendor/rest_variantValidator/docker-compose.yml \
    -f compose.vv-override.yml \
    up -d
```

First boot waits **~30 min** on PostgreSQL (UTA) initialization inside the VV stack. One-time; persists thereafter thanks to the override that bind-mounts `/var/lib/postgresql/data` to `${DATA_DIR}/variantvalidator/vvta-postgres`.

### 5. Smoke test

```bash
TOKEN='<name>.<secret>'
HOST='https://<your-host>:9443'

curl -sS -k "$HOST/healthz"
curl -sS -k -H "Authorization: Bearer $TOKEN" "$HOST/readyz" | jq .

curl -sS -k -X POST \
     -H "Authorization: Bearer $TOKEN" \
     -H 'Content-Type: application/json' \
     -d '{"genome_build":"GRCh38","variants":[
           {"id":"v1","gene":"SLC20A2","hgnc_id":11013,"variant":"NM_006749.5:c.1240G>T"}
         ]}' \
     "$HOST/v1/variants" | jq .
```

## License

This repository is MIT-licensed — see [LICENSE](LICENSE).

The service runs [VariantValidator](https://github.com/openvar/variantValidator) as an **unmodified sibling container**, communicating with it over HTTP. VariantValidator is **AGPL-3.0-only**; the AGPL terms apply to that component but not to the gateway code in this repository. See [ARCHITECTURE.md § "AGPL boundary"](ARCHITECTURE.md#agpl-boundary) for the precise constraints (which contributions are safe, which would poison the gateway's MIT license).

## Credits

The variant-string cleanup logic in `src/variant_lookup/normalize.py` is derived from Microsoft's [healthfutures-evagg](https://github.com/microsoft/healthfutures-evagg) (MIT). See [ARCHITECTURE.md § "Credits"](ARCHITECTURE.md#credits) for the full attribution list.
