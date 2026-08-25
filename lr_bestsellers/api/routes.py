"""The segments endpoint: one route, two branches.

``GET /v1/segments`` behaves differently depending on whether ``query`` is
supplied:

* ``query`` present → the question goes through the guarded RAG + Text2SQL
  pipeline and the response is an :class:`AgentAnswer`.
* ``query`` absent (or blank) → the CSV dump of the BigQuery features table is
  paged directly and the response is a :class:`CatalogPage`.
"""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Header, Query

from lr_bestsellers.api.dependencies import get_api_settings, get_catalog_repository
from lr_bestsellers.config import Settings
from lr_bestsellers.models.catalog import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    AgentAnswer,
    CatalogPage,
    ErrorResponse,
    PageRequest,
    SegmentsResult,
)
from lr_bestsellers.service import answer_query
from lr_bestsellers.store.protocols import CatalogRepositoryProtocol

log = structlog.get_logger(__name__)

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
