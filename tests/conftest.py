"""Test bootstrap — inject minimal env so Settings() can be instantiated without a real .env."""

import os
from pathlib import Path

# Set before any module imports Settings.
os.environ.setdefault("API_KEYS_FILE", str(Path("/tmp/api-keys.yaml")))
os.environ.setdefault("VV_BASE_URL", "http://variantvalidator.invalid:8000")
os.environ.setdefault("ECHTVAR_ARCHIVE", str(Path("/tmp/echtvar.zip")))
os.environ.setdefault("NCBI_EUTILS_EMAIL", "test@example.com")
