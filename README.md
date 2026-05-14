# variant-lookup

Self-hosted REST service for variant normalization and gnomAD v4 frequency lookups.

Replaces the chain of rate-limited / unreliable external services (Mutalyzer, VariantValidator, gnomAD GraphQL) typically used to turn messy LLM-extracted variant strings into normalized HGVS descriptions, GRCh38 pseudo-VCFs, and population frequencies.

See **[ARCHITECTURE.md](ARCHITECTURE.md)** for the system design, the public API contract, the configuration surface, and the AGPL boundary that future contributors must respect.

## Example

Given a genomic coordinate, the service walks it through Mutalyzer and VariantValidator to canonicalise the HGVS triple, then looks up the gnomAD v4 frequency:

```bash
curl -sS -k -X POST \
     -H "Authorization: Bearer <your-token>" \
     -H 'Content-Type: application/json' \
     -d '{"variant":"NC_000016.10:g.2116896C>A"}' \
     "https://<your-host>:9443/v1/variant" | jq
```

```json
{
  "meta": {
    "service": "0.1.0+dev",
    "reference": "GRCh38",
    "gnomad": "4.1",
    "variantvalidator": "3.0.2.dev235+ge5bb05951",
    "mutalyzer": "3.1.1",
    "timestamp": "2026-05-14T02:15:19.901260+00:00",
    "durations_ms": {
      "cleanup": 0,
      "rsid": 0,
      "normalize": 1851,
      "back_translate": 0,
      "variantvalidator": 970,
      "echtvar": 181,
      "total": 3002
    }
  },
  "normalized": [
    {
      "pseudo_vcf": "16-2116896-C-A",
      "hgvs_c": "NM_001009944.3:c.1543G>T",
      "hgvs_p": "NP_001009944.3:p.Gly515Trp",
      "frequency": {
        "ac": 1,
        "an": 1527530,
        "homozygote_count": 0,
        "heterozygote_count": 1,
        "hemizygote_count": 0,
        "faf95_popmax": null,
        "faf95_popmax_population": null
      }
    }
  ],
  "error": null
}
```

Any fully-qualified input works on its own — genomic (`NC_…:g.…`), coding (`NM_…:c.…`), protein (`NP_…:p.…`), or rsID (`rs28934578`). Unqualified shorthand (bare `c.…` / `p.…` or `GENE:c.…`) requires a `gene` field alongside `variant`. `normalized` is a list because protein-level inputs can back-translate to multiple coding variants; the other shapes resolve to a single entry. See [ARCHITECTURE.md § "Public API"](ARCHITECTURE.md#public-api) for the full schema, error codes, and retriable-vs-terminal failure semantics.

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

curl -sS -k "$HOST/healthz"                                           # gateway up
curl -sS -k -H "Authorization: Bearer $TOKEN" "$HOST/readyz" | jq .   # upstreams up
```

Once both probes are green, run the variant lookup from the [Example](#example) above end-to-end to confirm the full pipeline works.

## License

This repository is MIT-licensed — see [LICENSE](LICENSE).

The service runs [VariantValidator](https://github.com/openvar/variantValidator) as an **unmodified sibling container**, communicating with it over HTTP. VariantValidator is **AGPL-3.0-only**; the AGPL terms apply to that component but not to the gateway code in this repository. See [ARCHITECTURE.md § "AGPL boundary"](ARCHITECTURE.md#agpl-boundary) for the precise constraints (which contributions are safe, which would poison the gateway's MIT license).

## Credits

The variant-string cleanup logic in `src/variant_lookup/normalize.py` is derived from Microsoft's [healthfutures-evagg](https://github.com/microsoft/healthfutures-evagg) (MIT). See [ARCHITECTURE.md § "Credits"](ARCHITECTURE.md#credits) for the full attribution list.
