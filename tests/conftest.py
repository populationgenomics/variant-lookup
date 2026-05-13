"""Test bootstrap.

Sets up a minimal environment before any module imports `Settings`:
    - a temp api-keys.yaml with one known argon2id-hashed key
    - a tiny refseq_processed.json (so /v1/variants can load it)
    - placeholder paths for echtvar (missing on purpose, exercises the
      degraded `/readyz` path)
    - an unreachable VV URL (exercises the unreachable probe path)
"""

import json
import os
import tempfile
from pathlib import Path

import yaml
from argon2 import PasswordHasher

_TMPDIR = Path(tempfile.mkdtemp(prefix="vlookup-test-"))

# Bearer token format: `<name>.<secret>`.
TEST_KEY_NAME = "tester"
TEST_KEY_SECRET = "secret-for-tests-only"
TEST_BEARER = f"{TEST_KEY_NAME}.{TEST_KEY_SECRET}"

_keys_file = _TMPDIR / "api-keys.yaml"
_keys_file.write_text(
    yaml.safe_dump(
        {
            "keys": [
                {
                    "name": TEST_KEY_NAME,
                    "hash": PasswordHasher().hash(TEST_KEY_SECRET),
                }
            ]
        }
    )
)

_refseq_file = _TMPDIR / "refseq_processed.json"
_refseq_file.write_text(json.dumps({}))

os.environ.setdefault("API_KEYS_FILE", str(_keys_file))
os.environ.setdefault("VV_BASE_URL", "http://variantvalidator.invalid:8000")
os.environ.setdefault("ECHTVAR_ARCHIVES_DIR", str(_TMPDIR / "echtvar"))
os.environ.setdefault("REFSEQ_CACHE_PATH", str(_refseq_file))
os.environ.setdefault("NCBI_EUTILS_EMAIL", "test@example.com")
# mutalyzer_client._configure_retriever_cache() mkdir's this at import time;
# point it at a writable tmp dir so tests don't try to create /data/mutalyzer/cache.
os.environ.setdefault("MUTALYZER_CACHE_DIR", str(_TMPDIR / "mutalyzer-cache"))
