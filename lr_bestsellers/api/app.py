"""FastAPI application factory and domain exception handlers.

Run it with::

    uv run uvicorn lr_bestsellers.api.app:app --reload

Every domain exception is translated into an :class:`ErrorResponse` body so
callers can branch on a stable ``error`` code instead of parsing prose.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Final

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from lr_bestsellers.api.dependencies import get_api_settings
from lr_bestsellers.api.routes import router
from lr_bestsellers.exceptions import (
    BestSellersError,
    CatalogError,
    GuardrailError,
    InputGuardrailError,
)
from lr_bestsellers.models.catalog import ErrorResponse
from lr_bestsellers.utils.logging import configure_logging

log = structlog.get_logger(__name__)

API_TITLE: Final[str] = "LiveRamp Bestsellers Segment Intelligence API"
API_VERSION: Final[str] = "1.0.0"
API_DESCRIPTION: Final[str] = """
One endpoint, two behaviours.

* **Ask** — pass `query` and the question is routed through the guarded
  Agentic RAG + Text2SQL pipeline, returning a grounded answer with citations
  and the SQL that was executed.
* **Browse** — omit `query` and the endpoint pages the offline BigQuery dump of
  segment recommendation features, no LLM or BigQuery calls involved.

Responses are discriminated by the `mode` field (`agent` or `catalog`).
"""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Configure structured JSON logging for the lifetime of the application.

    Args:
        app: The running application (unused).

    Yields:
        Control back to the server while the application serves requests.
    """
    del app
    settings = get_api_settings()
    configure_logging(settings.log_level)
    log.info("api.startup", version=API_VERSION, catalog=str(settings.csv_catalog_path))
    yield
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
    """Map the ``BestSellersError`` hierarchy onto HTTP status codes.

    Handlers are resolved by walking the exception's MRO, so the most specific
    registration wins.

    Args:
        app: Application to attach the handlers to.
    """

    @app.exception_handler(InputGuardrailError)
    async def _handle_input_guardrail(
        request: Request,
        exc: InputGuardrailError,
    ) -> JSONResponse:
        """Return 400 — the caller's query was rejected before any LLM call."""
        del request
        log.warning("api.input_rejected", code=exc.code)
        return _error_response(400, exc.code, str(exc))

    @app.exception_handler(GuardrailError)
    async def _handle_guardrail(request: Request, exc: GuardrailError) -> JSONResponse:
        """Return 502 — a generated answer or statement failed a safety check."""
        del request
        log.error("api.answer_rejected", code=exc.code)
        return _error_response(502, exc.code, str(exc))

    @app.exception_handler(CatalogError)
    async def _handle_catalog(request: Request, exc: CatalogError) -> JSONResponse:
        """Return 503 — the CSV dump backing the browse branch is unusable."""
        del request
        log.error("api.catalog_unavailable", error=str(exc))
        return _error_response(503, "CATALOG_UNAVAILABLE", str(exc))

    @app.exception_handler(BestSellersError)
    async def _handle_domain(request: Request, exc: BestSellersError) -> JSONResponse:
        """Return 500 — any other failure inside the pipeline."""
        del request
        log.error("api.pipeline_error", error_type=type(exc).__name__, error=str(exc))
        return _error_response(500, "PIPELINE_ERROR", str(exc))


def create_app() -> FastAPI:
    """Build the FastAPI application with routes, docs, and error handlers.

    Returns:
        A configured application serving its OpenAPI schema at
        ``/openapi.json`` and Swagger UI at ``/docs``.

    Example:
        >>> app = create_app()
        >>> app.openapi()["info"]["version"]
        '1.0.0'
    """
    app = FastAPI(
        title=API_TITLE,
        version=API_VERSION,
        description=API_DESCRIPTION,
        openapi_url="/openapi.json",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )
    app.include_router(router)
    register_exception_handlers(app)
    return app


app = create_app()
"""Module-level application instance for ``uvicorn lr_bestsellers.api.app:app``."""
