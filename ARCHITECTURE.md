# Architecture

Self-hosted REST service for variant normalization and gnomAD v4 frequency lookups. Replaces a chain of rate-limited / unreliable external services (Mutalyzer, VariantValidator, gnomAD GraphQL) used by upstream callers extracting variants from biomedical literature.

This document is the source of truth for the system's shape, the boundaries between components, and the constraints (especially AGPL — see §"AGPL boundary") that future contributors must respect. Audience: LLM assistants and humans onboarding to the repo.

## Pipeline

Input: a batch of variant records `{id, gene, hgnc_id, variant, genome_build?}`, where `variant` is whatever messy HGVS-like or `rs…` string an LLM extracted from a paper.

Per-variant chain:

1. **Parse + clean** the raw `variant` string into a canonical HGVS description with a RefSeq accession. ~50 lines of cleanup regex handle missing prefixes, single↔three-letter amino-acid codes, embedded gene symbols, stray punctuation, etc. _Derived from Microsoft's `healthfutures-evagg` (MIT)._
2. **If input is `rs…`**: call NCBI E-utils `efetch?db=snp` (HTTP, external) to resolve the rsID to a canonical HGVS description.
3. **Normalize** the HGVS description by calling `mutalyzer.normalizer.normalize(...)` (in-process — the Mutalyzer Python library is MIT-licensed).
4. **If input is `p.…`**: call `mutalyzer.back_translator.back_translate(...)` (in-process). Back-translation can return *multiple* coding variants for one protein change (different codons), each of which is carried forward and returned in the response — no policy choice is baked in at the service layer.
5. **HGVS → genomic coordinates**: call VariantValidator (HTTP, sibling container) at `/VariantValidator/variantvalidator/GRCh38/{hgvs}/mane_select`. VV returns the GRCh38 pseudo-VCF (`chrom-pos-ref-alt`) plus the MANE-select HGVS-c / HGVS-p strings. For inputs that arrived as GRCh37 chromosomal coordinates, the cross-assembly projection happens implicitly inside VV (we always request `/GRCh38/`).
6. **Pseudo-VCF → frequencies**: group the pseudo-VCFs by chromosome, then for each chromosome present in the batch, write a tiny sorted VCF and subprocess `echtvar anno` against the matching per-chromosome archive (`gnomad.joint.v4.1.chr{chrom}.echtvar.zip`). The per-chromosome subprocess calls run concurrently via a `ThreadPoolExecutor`. echtvar's binary search inside 1 MB chunks gives ~1M lookups/sec offline. The encoded data is sharded by chromosome because categorical-INFO string tables in echtvar are keyed by insertion order per VCF — merging across chromosomes would silently misalign indices unless we decoded + remapped them.
7. **Hemizygote derivation**: echtvar exposes `AC_joint_XY` but not a hemizygote count; we compute it in-process (0 for autosomes + PAR regions, `AC_XY` elsewhere on chrX/Y).
8. **Response assembly**: combine normalized HGVS, pseudo-VCF, frequencies, and per-variant errors into the response object. Versions of every component are stamped in `meta`.

## What runs where

Two services in compose (plus an nginx sidecar for TLS):

| Service | Purpose | Process boundary | License |
|---|---|---|---|
| `gateway` (FastAPI, ours) | REST API, parsing/cleanup, Mutalyzer calls (in-process), echtvar subprocess, NCBI HTTP calls, VV HTTP calls, response assembly | one Python process | MIT |
| `variantvalidator` (upstream image, unmodified) | HGVS → GRCh38 pseudo-VCF + MANE-select transcripts | sibling container, HTTP only | **AGPL-3.0-only** |
| `nginx` (sidecar) | TLS termination, reverse proxy in front of `gateway` | sibling container | BSD-2-Clause |

Mutalyzer is **in-process** because the library is MIT-licensed and importable. echtvar is **subprocess** because it's a CLI tool; the wall-clock overhead is `max(per-subprocess startup)` across the chromosomes in the batch (each ~30-100 ms for fork + binary load + zip open of a ~150 MB per-chrom archive), since the dispatch is parallel. VariantValidator is **HTTP-only across a container boundary** because it is AGPL — see §"AGPL boundary".

VariantValidator's deployment has internal dependencies (MySQL + PostgreSQL + SeqRepo containers) that come from the upstream `docker-compose.yml`. We **vendor `rest_variantValidator` under `vendor/`** and bring the upstream compose stack up alongside ours. The vendored sources are **not part of our service** — they belong to the AGPL surface and live outside `src/`.

**We track upstream `master` for the VV stack, not a pinned SHA.** This is a forced compromise, not a choice: `rest_variantValidator/pyproject.toml` pins its three sub-libraries (`vvhgvs`, `VariantFormatter`, `VariantValidator`) to `@master`, so `pip install -e .` in their Dockerfile pulls each one's current master HEAD at image build time. The only way to lock those transitive versions would be to patch upstream's `pyproject.toml` — a modification that triggers AGPL §13 on the resulting image. So pinning a SHA on the REST wrapper alone would give us a false sense of reproducibility while the three sub-libraries float anyway. We accept the float across all four repos and capture actually-deployed versions by reading them from the running service's `/` banner at gateway startup (surfaced in `/v1/variants` responses' `meta.variantvalidator`).

This is genuinely unsatisfactory — every rebuild is potentially a different stack. Two paths out, neither in scope for v1:

- **Fork the VV repos** under AGPL and pin transitive deps in the fork. Our gateway code stays MIT because the network boundary still holds; we just take on the maintenance burden of a fork.
- **Replace VariantValidator** entirely with a permissively-licensed alternative. Candidates:
  - [`hgvs-weaver`](https://github.com/folded/hgvs-weaver) — MIT, Rust-backed Python library, type-safe coordinate handling, supports g/c/p parsing + mapping + normalization. Likely the strongest fit, but doesn't cover cross-assembly liftover yet, so a replacement plan would have to add that upstream or layer a separate liftover step (CrossMap / pyliftover). Restricting inputs to GRCh38 is not acceptable — see §"Pipeline" step 5.
  - biocommons `hgvs` (Apache-2.0) — mature, Python-only, but doesn't cover everything VV does out of the box (e.g. MANE-select transcript selection).

The replacement path is the higher-priority longer-term change.

**Note for future contributors**: the SHA strings the public `rest.variantvalidator.org` banner shows for `rest_variantValidator` and `variantValidator` (e.g. `7edab06bc`, `b64f3e1fb` as of mid-2026) are **not reachable** in the public openvar repos — they live on a private branch or fork. The production VV-stack is not bit-for-bit reproducible from public sources, even if we wanted to be.

## Public API

### `POST /v1/variants`

Authenticated bulk lookup. Max 1000 variants per request (rejected with 413 above that).

Request:

```json
{
  "genome_build": "GRCh38",
  "variants": [
    {"id": "v1", "gene": "SLC20A2", "hgnc_id": 11013, "variant": "c.1240G>T"},
    {"id": "v2", "gene": "PDGFB",   "hgnc_id": 8804,  "variant": "p.Arg191*"}
  ]
}
```

Response (always 200 unless the request itself is malformed, auth fails, or the service is broken):

```json
{
  "meta": {
    "service": "0.1.0+abc123",
    "reference": "GRCh38",
    "gnomad": "4.1",
    "variantvalidator": "2.2.0",
    "mutalyzer": "3.0.4",
    "timestamp": "2026-05-13T..."
  },
  "results": [
    {
      "id": "v1",
      "input": {"gene": "SLC20A2", "hgnc_id": 11013, "variant": "c.1240G>T"},
      "normalized": [
        {
          "pseudo_vcf": "8-42437272-C-A",
          "hgvs_c": "NM_001257180.2:c.1240G>T",
          "hgvs_p": "NP_001244109.1:p.Glu414Ter",
          "frequency": {
            "ac": 0, "an": 1614174, "homozygote_count": 0,
            "hemizygote_count": 0, "faf95_popmax": null, "faf95_popmax_population": null
          }
        }
      ],
      "error": null
    },
    {
      "id": "v2",
      "input": {"gene": "PDGFB", "hgnc_id": 8804, "variant": "p.Arg191*"},
      "normalized": null,
      "error": {
        "code": "NORMALIZATION_FAILED",
        "upstream": "mutalyzer",
        "message": "..."
      }
    }
  ]
}
```

Key contract points:

- **`normalized` is a list**, even for non-ambiguous inputs. Protein variants can back-translate to multiple coding variants; the service returns them all and leaves selection (max-AC, all, etc.) to the caller.
- **Per-variant `error` field** is the failure surface. The HTTP response stays 200 even if every variant in the batch fails; callers iterate.
- **`meta` is always present** and contains the versions of every component used to produce the results, so callers can pin/cite/reproduce.
- **`variant_not_found` in gnomAD** is not an error — it's a `frequency` object with `ac: 0` and nulls for FAF. Distinguishable from an actual gnomAD lookup error by inspecting `error`.

### `GET /healthz`

Returns 200 with `{"status": "ok"}` if the FastAPI process is alive. Used by docker-compose healthchecks.

### `GET /readyz`

Returns 200 with a per-upstream breakdown if everything is reachable; 503 if any required upstream is down.

```json
{
  "status": "ready",
  "upstreams": {
    "variantvalidator": {"status": "ok", "http": "200"},
    "echtvar_archives": {"status": "ok", "path": "/data/echtvar"},
    "refseq_cache": {"status": "ok", "path": "/data/refseq/refseq_processed.json"}
  }
}
```

If one or more of the 24 expected per-chromosome echtvar archives are missing,
`echtvar_archives` reports `status: "incomplete"` with `missing_chroms` set to
a comma-separated list (e.g. `"X,Y"`).

### `GET /docs`, `GET /openapi.json`

FastAPI-generated. Behind the same API-key auth as everything else.

### Auth

`Authorization: Bearer <api-key>`. Keys are random high-entropy tokens. The server stores **argon2id hashes** (per-key salt embedded in the hash string) in a config file mounted from outside the container. Reload requires a service restart. No account management, no JWT issuance — adding/revoking a key is editing the file. No rate limiting in v1.

### Errors at the HTTP layer

- `400` — malformed request body
- `401` — missing/invalid bearer token
- `413` — batch larger than 1000
- `503` — service or required upstream unavailable (paired with `/readyz` failing)
- `500` — internal error (bug in the gateway)

Per-variant errors are *never* at the HTTP layer — always `200` with `results[i].error` populated.

## Configuration

All deployment-specific values come from a `.env` file that is **not** committed. Nothing about the host, cluster, or cert layout leaks into committed code. The full set:

```
# Storage
DATA_DIR=/some/host/path                  # parent dir containing all reference data
SSL_CERT_FILE=/some/host/path/server-cert-plus-intermediates.pem
SSL_KEY_FILE=/some/host/path/server.key   # may be root-only readable; nginx-as-root in container reads it via the bind mount
API_KEYS_HOST_FILE=/some/host/path/api-keys.yaml

# Network
NGINX_PORT=9443                           # external HTTPS port

# Upstreams
NCBI_EUTILS_EMAIL=...                     # required by NCBI
NCBI_EUTILS_API_KEY=...                   # optional, raises NCBI rate limit

# (No VariantValidator SHA pins — we track upstream master across the stack
# because we can't lock transitive deps without patching AGPL code.
# See "What runs where" for the rationale.)

# Pinned echtvar precompiled release (for the gateway image build)
ECHTVAR_VERSION=v0.2.4

# Versions stamped into responses
SERVICE_VERSION=0.1.0+<git-sha>
GNOMAD_VERSION=4.1
VARIANTVALIDATOR_VERSION=...              # derived from pinned SHAs
MUTALYZER_VERSION=...                     # set per pip-installed version
```

## Reference data layout

Single `${DATA_DIR}` host mount, subdirectories per dataset, mounted read-only into the gateway except where the upstream tool writes:

```
${DATA_DIR}/
  echtvar/
    gnomad.joint.v4.1.chr1.echtvar.zip  # 24 per-chromosome archives.
    gnomad.joint.v4.1.chr2.echtvar.zip  # The gateway dispatches lookups
    ...                                 # to the matching chromosome's archive.
    gnomad.joint.v4.1.chrY.echtvar.zip
  refseq/
    refseq_processed.json               # MANE-Select / RefSeq-Select index by gene symbol
                                        # (also indexed by versionless accession for autocomplete)
  variantvalidator/                     # belongs to the AGPL VV service.
                                        # Upstream's defaults point at $HOME and
                                        # leave the DBs in the container layer;
                                        # compose.vv-override.yml redirects them
                                        # to here and adds persistence.
    seqdata/                            # SeqRepo, bind-mounted into rv-seqrepo + rest
    logs/                               # rest service log files
    vdb-mysql/                          # MySQL data (validator DB, ~30 min to re-init)
    vvta-postgres/                      # PostgreSQL data (UTA, ~30 min to re-init)
```

In-container paths are `/data/<dataset>/...` — code references only those, never the host path.

Reference data is **manually refreshed** in v1. A single `scripts/setup.sh` script handles both first-time bootstrap and per-dataset refresh via subcommands (`vendor-vv`, `ensure-vv-dirs`, `build-vv`, `build-gateway`, `refresh-echtvar`, `refresh-refseq`, `cleanup-echtvar-staging`). Each step is idempotent — re-running skips work that's already done.

| Dataset | Source | Refresh trigger | Subcommand | Approx work |
|---|---|---|---|---|
| echtvar archives (24 per-chrom) | gnomAD release notes | gnomAD point releases (rare) | `refresh-echtvar` | ~3-5 h: ~800 GB S3 sync, then per-chromosome parallel encode of 24 VCFs |
| refseq_processed.json | NCBI RefSeq GFF | New GRCh38 patch (every ~6 mo) | `refresh-refseq` | minutes |
| Mutalyzer cache | NCBI on first use | Auto-warmed; manual flush if NCBI changes a record | n/a (auto) | n/a (lives in a docker-managed named volume) |
| VV seqdata / vvta / vdb | VV upstream master | Re-running `vendor-vv` fetches latest master | `vendor-vv` + `build-vv` | ~1 h compile + ~30 min db init |
| Staging VCFs (post-encode) | n/a | After all 24 echtvar archives exist | `cleanup-echtvar-staging` | seconds; reclaims ~800 GB |

## Versioning

URL versioning at `/v1/`. Breaking response-shape changes get a new prefix. Data-layer versions go into the `meta` block, so the same `/v1/` endpoint can return data from different gnomAD releases over time — callers should record the `meta.gnomad` value alongside the results.

## AGPL boundary

VariantValidator is licensed **AGPL-3.0-only**. Our service is MIT. The boundary that keeps these compatible is the **HTTP-over-network call** between the `gateway` container and the `variantvalidator` container.

### What this means in practice — DO

- Run the upstream `variantvalidator` Docker image **unmodified**. Pin a specific upstream tag.
- Speak to it via HTTP only, exactly like any external API.
- Document the AGPL dependency in `README.md` and `ARCHITECTURE.md`. Link to the upstream source.
- Ship the gateway image and VV image as **separate** images, each with their own license metadata.

### What this means in practice — DON'T

- **DON'T** `pip install` VariantValidator into the gateway image — that would link our code to AGPL code in-process and trigger the copyleft.
- **DON'T** `from VariantValidator import ...` anywhere in the gateway code, for the same reason.
- **DON'T** bundle VV's source files, binaries, or databases into the gateway image build context.
- **DON'T** modify VariantValidator. Any patch to VV must be applied in a fork that is itself AGPL — and AGPL §13 then requires the fork's source to be available to anyone interacting with the service over the network.
- **DON'T** remove the AGPL attribution / upstream-source link from documentation.

### Why Mutalyzer is different

Mutalyzer is MIT-licensed. Importing `mutalyzer` directly in the gateway is fine and doesn't constrain our license. If Mutalyzer ever relicenses (e.g. to GPL/AGPL), revisit and move it across the same HTTP boundary VV sits behind.

### If the boundary needs to move

If at some point we want to bundle, patch, or extend VariantValidator's behavior, the choices are:
- Accept that the combined service goes AGPL (and offer source under §13). Possible but a real policy decision.
- Switch to an alternative library with a permissive license — biocommons `hgvs` (Apache-2.0) covers some of VV's responsibilities; not a drop-in.

Neither is in scope for v1.

## Components and responsibilities

### `gateway` (this repo)

- `src/variant_lookup/api.py` — FastAPI routes, auth, request validation, response shaping, `meta` assembly.
- `src/variant_lookup/pipeline.py` — orchestrates the per-variant chain (parse → normalize → coords → freq).
- `src/variant_lookup/normalize.py` — variant-string cleanup regex + HGVS construction.
- `src/variant_lookup/mutalyzer_client.py` — in-process wrapper around `mutalyzer.normalizer.normalize` and `mutalyzer.back_translator.back_translate`. Translates Mutalyzer's error shapes into our `error` codes.
- `src/variant_lookup/variantvalidator_client.py` — HTTP client for the sibling VV container.
- `src/variant_lookup/echtvar.py` — sorted-VCF generation, `echtvar anno` subprocess, annotated-VCF parsing, hemizygote derivation.
- `src/variant_lookup/ncbi.py` — rsID resolution against NCBI E-utils. External HTTP. API key in env.
- `src/variant_lookup/refseq.py` — in-process `refseq_processed.json` lookup. Resolves gene symbol → MANE-Select transcript/protein/genomic accession, and versionless accession → versioned (replaces NCBI's accession-autocomplete).
- `src/variant_lookup/auth.py` — argon2id key verification.
- `src/variant_lookup/health.py` — `/healthz`, `/readyz`.
- `src/variant_lookup/config.py` — env-var loading and validation. **All paths and URLs come from env.**
- `scripts/setup.sh` — one-shot bootstrap and per-dataset refresh; see "Reference data layout" for the subcommands.

### `variantvalidator` (vendored upstream sources)

- `rest_variantValidator` cloned under `vendor/` at `origin/master`. `scripts/setup.sh vendor-vv` checks it out; `scripts/setup.sh build-vv` builds its docker images.
- The image build pulls `variantValidator`, `variantFormatter`, and `vv_hgvs` from each repo's master HEAD via pip — see "What runs where" for why we don't pin.
- Includes the VV REST API container, MySQL, PostgreSQL (UTA), and SeqRepo.
- Bind-mounts from `${DATA_DIR}/variantvalidator/*` for persistence.
- Health-checked by the gateway's `/readyz`.

### `nginx` (sidecar)

- TLS termination on `${NGINX_PORT:-9443}`.
- Cert + key bind-mounted **individually** at `/etc/nginx/ssl/cert.pem` and `/etc/nginx/ssl/key.pem` from `${SSL_CERT_FILE}` and `${SSL_KEY_FILE}` on the host. nginx's master process runs as root inside the container so a root-only host key file is readable without copying.
- Reverse-proxies all paths to `gateway:8000` over plain HTTP on the internal Docker network.
- Conf file (`nginx.conf`) committed; cert paths referenced only by their in-container locations.

## Logging

All services log JSON to stdout. Gateway log lines carry:
- `timestamp`, `level`, `service`, `request_id` (per-request UUID)
- `event` — high-cardinality discriminator (`request_received`, `upstream_call`, `variant_error`, …)
- structured fields per event (`upstream`, `latency_ms`, `variant_id`, `error_code`, …)

No PII; we don't log API keys or full bearer tokens (truncate to first 8 chars + length).

## Out of scope for v1

These are deferred deliberately, not forgotten:

- **Caching**. Variants recur across papers; a normalization-input → result cache (SQLite or Redis) is the natural next optimization. Add it once per-variant latency is shown to be the bottleneck.
- **Rate limiting per API key**. Internal use only; we'll add a per-key token bucket if abuse emerges.
- **echtvar Python bindings**. The library API (`EchtVars::open` + `update_expr_values` + `Variant` trait) is small and PyO3 bindings are ~200 lines of Rust — feasible but the ~10-30ms subprocess overhead per batch is not the bottleneck given upstream latencies. Revisit if echtvar overhead dominates.
- **Async / streaming responses**. The sync bulk POST fits 100 v/s bursty traffic fine. Move to streaming or async jobs only if a single request needs > a few seconds.
- **Mitochondrial variants (chrM)**. gnomAD treats these as a separate dataset with different schema. Inputs with `m.` HGVS will currently fail at the frequency-lookup step. Not in scope; add when needed.
- **Caller-supplied selection policies** (max-AC, etc.). The service returns all back-translations; callers pick.

## Credits

- **Microsoft `healthfutures-evagg`** ([github.com/microsoft/healthfutures-evagg](https://github.com/microsoft/healthfutures-evagg), MIT) — portions of the variant cleanup + normalization logic in `src/variant_lookup/normalize.py` are derived from this project.
- **Mutalyzer** (`mutalyzer/mutalyzer`, MIT, Leiden University Medical Center) — used in-process for HGVS normalization and protein-to-coding back-translation.
- **VariantValidator** (`openvar/variantValidator` and `openvar/rest_variantValidator`, **AGPL-3.0-only**, Leicester) — used as a sibling container for HGVS-to-genomic-coordinates resolution. See §"AGPL boundary".
- **echtvar** (`brentp/echtvar`, MIT, Brent Pedersen) — used as a subprocess for offline gnomAD frequency annotation.
- **gnomAD v4.1** (Broad Institute, public-domain data) — frequency data, encoded into the echtvar archive once and looked up offline.
- **NCBI E-utilities** — used externally over HTTPS for rsID → HGVS resolution.
- **NCBI RefSeq** — public-domain reference data, processed into a local gene-symbol-and-accession index.
