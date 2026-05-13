"""API-key authentication.

Bearer token format: ``<name>.<random-secret>``. The name identifies the caller
(used for logs and to look up the matching argon2id hash); the secret is a
high-entropy random string. Keys are stored as argon2id-hashed entries in a
YAML file mounted from outside the container, e.g.::

    keys:
      - name: palit
        hash: $argon2id$v=19$m=65536,t=3,p=4$...$...
"""

from pathlib import Path
from typing import Annotated

import yaml
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, Header, HTTPException, status

from variant_lookup.config import Settings, get_settings

_hasher = PasswordHasher()
_keys_cache: dict[Path, dict[str, str]] = {}


def load_keys(keys_file: Path) -> dict[str, str]:
    """Return the ``name -> argon2id-hash`` mapping. Cached per file path."""
    if keys_file in _keys_cache:
        return _keys_cache[keys_file]
    with keys_file.open() as f:
        data = yaml.safe_load(f) or {}
    keys = {entry["name"]: entry["hash"] for entry in data.get("keys", [])}
    _keys_cache[keys_file] = keys
    return keys


def clear_cache() -> None:
    """Drop the in-memory keys cache. Call after editing api-keys.yaml."""
    _keys_cache.clear()


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_api_key(
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    """Verify ``Authorization: Bearer <name>.<secret>``. Returns the caller name."""
    if not authorization or not authorization.startswith("Bearer "):
        raise _unauthorized("missing or malformed Authorization header")

    token = authorization.removeprefix("Bearer ").strip()
    if "." not in token:
        raise _unauthorized("malformed token")

    name, secret = token.split(".", 1)
    keys = load_keys(settings.api_keys_file)
    hash_ = keys.get(name)
    if hash_ is None:
        raise _unauthorized("invalid API key")

    try:
        _hasher.verify(hash_, secret)
    except VerifyMismatchError as e:
        raise _unauthorized("invalid API key") from e

    return name
