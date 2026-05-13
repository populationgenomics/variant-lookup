# variant-lookup

Self-hosted REST service for variant normalization and gnomAD v4 frequency lookups.

Replaces the chain of rate-limited / unreliable external services (Mutalyzer, VariantValidator, gnomAD GraphQL) typically used to turn messy LLM-extracted variant strings into normalized HGVS descriptions, GRCh38 pseudo-VCFs, and population frequencies.

See **[ARCHITECTURE.md](ARCHITECTURE.md)** for the system design, the public API contract, the configuration surface, and the AGPL boundary that future contributors must respect.

## License

This repository is MIT-licensed — see [LICENSE](LICENSE).

The service runs [VariantValidator](https://github.com/openvar/variantValidator) as an **unmodified sibling container**, communicating with it over HTTP. VariantValidator is **AGPL-3.0-only**; the AGPL terms apply to that component but not to the gateway code in this repository. See [ARCHITECTURE.md § "AGPL boundary"](ARCHITECTURE.md#agpl-boundary) for the precise constraints (which contributions are safe, which would poison the gateway's MIT license).

## Credits

The variant-string cleanup logic in `app/normalize.py` is derived from Microsoft's [healthfutures-evagg](https://github.com/microsoft/healthfutures-evagg) (MIT). See [ARCHITECTURE.md § "Credits"](ARCHITECTURE.md#credits) for the full attribution list.
