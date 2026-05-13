"""API-key authentication — argon2id hashes loaded from a mounted YAML file."""

from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from variant_lookup.config import Settings, get_settings


def require_api_key(
    _settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    """Verify `Authorization: Bearer <api-key>`. Returns the verified caller name on success."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing or malformed Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="API key verification not yet implemented",
    )
