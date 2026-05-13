"""ASGI entry point — `uvicorn variant_lookup.main:app`."""

from variant_lookup.api import create_app

app = create_app()
