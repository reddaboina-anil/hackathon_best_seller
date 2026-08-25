"""HTTP layer: a FastAPI application exposing the segments endpoint.

The OpenAPI schema is served at ``/openapi.json`` with interactive docs at
``/docs`` (Swagger UI) and ``/redoc``.
"""

from __future__ import annotations

from lr_bestsellers.api.app import create_app

__all__ = ["create_app"]
