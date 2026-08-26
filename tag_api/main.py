"""FastAPI application factory for the standalone tag API.

Run it with::

    uv run uvicorn main:app --host 0.0.0.0 --port 8001

The DuckDB connection is opened once in ``lifespan`` and closed on shutdown.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Final

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from config import get_settings
from dependencies import get_tag_store
from exceptions import TagNotFoundError, TagStoreError
from models import ErrorResponse, HealthStatus
from routes import router

log = structlog.get_logger(__name__)

API_TITLE: Final[str] = "LiveRamp Segment Tag API"
API_VERSION: Final[str] = "0.1.0"
API_DESCRIPTION: Final[str] = """
Pre-computed recommendation tags for syndicated segments (Best Seller-style
badges). Backed by a local DuckDB file — no LLM, no BigQuery, no secrets.

* `GET /healthz` — liveness probe (no DuckDB)
* `GET /v1/tags` — list every tag definition
* `GET /v1/segments` — paginated dump rows with tags
* `GET /v1/segments/{segment_id}/tags` — tags on one segment
* `GET /v1/tags/{slug}/segments` — paginated segment IDs for a tag
"""


def _configure_logging() -> None:
    """Configure structlog for newline-delimited JSON on stdout."""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open the tag store once and close it on shutdown.

    Args:
        app: The running application; ``app.state.tag_store`` is populated.

    Yields:
        Control back to the server while the application serves requests.
    """
    _configure_logging()
    store = get_tag_store()
    app.state.tag_store = store
    log.info(
        "api.startup",
        version=API_VERSION,
        duckdb=str(get_settings().tags_duckdb_path),
        store_type=type(store).__name__,
    )
    yield
    closer = getattr(store, "close", None)
    if callable(closer):
        closer()
    get_tag_store.cache_clear()
    log.info("api.shutdown")


def _error_response(status_code: int, code: str, detail: str) -> JSONResponse:
    """Build a JSON error response with a stable machine-readable code.

    Args:
        status_code: HTTP status code to return.
        code: Machine-readable error code.
        detail: Human-readable explanation.

    Returns:
        JSON response carrying an ``ErrorResponse`` body.
    """
    payload = ErrorResponse(error=code, detail=detail)
    return JSONResponse(status_code=status_code, content=payload.model_dump())


def register_exception_handlers(app: FastAPI) -> None:
    """Map tag-api exceptions onto HTTP status codes.

    Args:
        app: Application to attach the handlers to.
    """

    @app.exception_handler(TagNotFoundError)
    async def _handle_not_found(request: Request, exc: TagNotFoundError) -> JSONResponse:
        """Return 404 — the requested tag slug is not defined."""
        del request
        log.warning("api.tag_not_found", error=str(exc))
        return _error_response(404, "TAG_NOT_FOUND", str(exc))

    @app.exception_handler(TagStoreError)
    async def _handle_store(request: Request, exc: TagStoreError) -> JSONResponse:
        """Return 503 — the DuckDB tag store is unreadable."""
        del request
        log.error("api.tag_store_unavailable", error=str(exc))
        return _error_response(503, "TAG_STORE_UNAVAILABLE", str(exc))


def create_app() -> FastAPI:
    """Build the FastAPI application with routes, docs, and error handlers.

    Returns:
        A configured application serving its OpenAPI schema at
        ``/openapi.json`` and Swagger UI at ``/docs``.

    Example:
        >>> app = create_app()
        >>> app.openapi()["info"]["version"]
        '0.1.0'
    """
    application = FastAPI(
        title=API_TITLE,
        version=API_VERSION,
        description=API_DESCRIPTION,
        openapi_url="/openapi.json",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )
    application.include_router(router)
    register_exception_handlers(application)

    @application.get(
        "/healthz",
        response_model=HealthStatus,
        summary="Liveness probe",
        tags=["health"],
    )
    def healthz() -> HealthStatus:
        """Return ok once Uvicorn is accepting connections.

        Returns:
            A ``HealthStatus`` with ``status='ok'``. DuckDB is not consulted.
        """
        return HealthStatus()

    return application


app = create_app()
"""Module-level application instance for ``uvicorn main:app``."""
