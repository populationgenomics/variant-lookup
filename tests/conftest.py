"""Test bootstrap.

Sets up a minimal environment before any module imports `Settings`:
    - a temp api-keys.yaml with one known argon2id-hashed key
    - placeholder paths for echtvar/refseq (missing on purpose, exercises the
      degraded `/readyz` path)
    - an unreachable VV URL (exercises the unreachable probe path)
"""

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

os.environ.setdefault("API_KEYS_FILE", str(_keys_file))
os.environ.setdefault("VV_BASE_URL", "http://variantvalidator.invalid:8000")
os.environ.setdefault("ECHTVAR_ARCHIVE", str(_TMPDIR / "echtvar.zip"))
os.environ.setdefault("REFSEQ_CACHE_PATH", str(_TMPDIR / "refseq_processed.json"))
os.environ.setdefault("NCBI_EUTILS_EMAIL", "test@example.com")
