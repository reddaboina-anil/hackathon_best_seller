"""Route handlers for the ``/v1`` prefix.

Two existing branches on ``GET /v1/segments``:

* ``query`` present → guarded RAG + Text2SQL pipeline → :class:`AgentAnswer`.
* ``query`` absent → CSV catalog browse → :class:`CatalogPage`.

New endpoints added here:

* ``POST /v1/query`` → structured :class:`SegmentQueryResponse` with each
  segment as a typed object (no markdown prose to parse).
* ``GET /v1/health`` → lightweight liveness check.
"""

from __future__ import annotations

import time
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Header, Query

from lr_bestsellers.api.dependencies import get_api_settings, get_catalog_repository
from lr_bestsellers.api.response_builder import build_segment_query_response
from lr_bestsellers.config import Settings
from lr_bestsellers.models.catalog import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    AgentAnswer,
    CatalogPage,
    ErrorResponse,
    HealthResponse,
    PageRequest,
    SegmentsResult,
)
from lr_bestsellers.models.query import (
    QueryTextRequest,
    SegmentQueryResponse,
)
from lr_bestsellers.service import answer_query
from lr_bestsellers.store.protocols import CatalogRepositoryProtocol

log = structlog.get_logger(__name__)

_API_VERSION: str = "1.0.0"

router = APIRouter(prefix="/v1", tags=["segments"])

_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    400: {"model": ErrorResponse, "description": "Input guardrail rejected the query."},
    500: {"model": ErrorResponse, "description": "Unexpected pipeline failure."},
    502: {"model": ErrorResponse, "description": "Answer rejected by an output guardrail."},
    503: {"model": ErrorResponse, "description": "CSV catalog is missing or malformed."},
}


@router.get(
    "/segments",
    response_model=SegmentsResult,
    summary="Ask a question about segments, or browse the segment catalog",
    response_description="An agent answer when `query` is set, otherwise a page of catalog rows.",
    responses=_ERROR_RESPONSES,
)
def get_segments(
    settings: Annotated[Settings, Depends(get_api_settings)],
    catalog: Annotated[CatalogRepositoryProtocol, Depends(get_catalog_repository)],
    query: Annotated[
        str | None,
        Query(
            max_length=2000,
            description=(
                "Plain-English question. When omitted or blank, the endpoint pages "
                "the segment catalog instead of running the agent."
            ),
            examples=["Which segments earn the most provider revenue?"],
        ),
    ] = None,
    page: Annotated[
        int,
        Query(ge=1, description="1-based page number. Ignored when `query` is set."),
    ] = 1,
    page_size: Annotated[
        int,
        Query(
            ge=1,
            le=MAX_PAGE_SIZE,
            description="Rows per page. Ignored when `query` is set.",
        ),
    ] = DEFAULT_PAGE_SIZE,
    caller_id: Annotated[
        str,
        Header(alias="X-Caller-Id", description="Rate-limit bucket key for agent queries."),
    ] = "api",
) -> AgentAnswer | CatalogPage:
    """Answer a question with the agent, or return a page of the CSV catalog.

    Args:
        settings: Injected application settings.
        catalog: Injected repository over the CSV dump.
        query: Optional plain-English question.
        page: 1-based page number for the catalog branch.
        page_size: Rows per page for the catalog branch.
        caller_id: Rate-limit bucket key for the agent branch.

    Returns:
        ``AgentAnswer`` when ``query`` holds a question, otherwise ``CatalogPage``.

    Raises:
        InputGuardrailError: When input guardrails reject the query.
        OutputGuardrailError: When output guardrails reject the answer.
        CatalogError: When the CSV dump cannot be read.
    """
    question = (query or "").strip()
    if not question:
        log.info("api.segments.browse", page=page, page_size=page_size)
        return catalog.page(PageRequest(page=page, page_size=page_size))

    log.info("api.segments.ask", caller_id=caller_id, query=question[:50])
    return AgentAnswer(
        query=question,
        result=answer_query(question, settings, caller_id),
    )


@router.post(
    "/query",
    response_model=SegmentQueryResponse,
    summary="Query segments — structured response",
    response_description=(
        "Each matched segment as a typed object with rank, name, description, "
        "rank metrics, and platform lists. No markdown to parse."
    ),
    responses=_ERROR_RESPONSES,
    tags=["query"],
)
def post_query(
    body: QueryTextRequest,
    settings: Annotated[Settings, Depends(get_api_settings)],
) -> SegmentQueryResponse:
    """Submit a plain-English question and receive machine-readable segment objects.

    The pipeline is identical to the agent branch of ``GET /v1/segments``:
    input guardrails → classify intent → hybrid search / Text2SQL → synthesize
    → output guardrails.  The difference is in the response: instead of a prose
    markdown string, each segment is returned as a typed ``SegmentResult`` with
    explicit fields for name, ID, description, rank metrics, and platform lists.

    The ``narrative`` field still carries the LLM-generated prose for UIs that
    want to display a human-readable summary alongside the structured data.

    Args:
        body: Request body containing ``query`` and optional ``caller_id``.
        settings: Injected application settings.

    Returns:
        ``SegmentQueryResponse`` with structured segment objects.

    Raises:
        InputGuardrailError: When input guardrails reject the query.
        OutputGuardrailError: When output guardrails reject the answer.

    Example:
        POST /v1/query
        {"query": "Frequent international travelers, business class preferred"}
    """
    log.info("api.query.start", caller_id=body.caller_id, query=body.query[:50])
    t0 = time.monotonic()
    raw = answer_query(body.query, settings, body.caller_id)
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    log.info(
        "api.query.complete",
        intent=raw.intent,
        confidence=raw.confidence,
        sql_rows=len(raw.sql_results),
        elapsed_ms=elapsed_ms,
    )
    return build_segment_query_response(body.query, raw, elapsed_ms)


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Liveness check",
    response_description="``{\"status\": \"ok\"}`` when the process is healthy.",
    tags=["ops"],
)
def get_health() -> HealthResponse:
    """Return a simple liveness response.

    This endpoint performs no I/O — it exists solely so load balancers and
    container orchestrators can confirm the process is alive.  For a readiness
    check that verifies downstream connectivity (Qdrant, BigQuery), extend this
    handler or add a separate ``/readyz`` endpoint.

    Returns:
        ``HealthResponse`` with ``status="ok"`` and the current API version.
    """
    return HealthResponse(status="ok", version=_API_VERSION)
