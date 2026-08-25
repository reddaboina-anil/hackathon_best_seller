"""lr-bestsellers — Hybrid Agentic RAG + Text2SQL for LiveRamp syndicated segments.

Public API surface: :func:`query` and :func:`ingest`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lr_bestsellers.models.query import QueryRequest, QueryResponse

__all__ = ["query", "ingest"]


def query(request: QueryRequest) -> QueryResponse:
    """Submit a plain-English question and receive a grounded, cited answer.

    Routes the request through the LangGraph agent (classify → retrieve / SQL
    → synthesize) and returns a structured ``QueryResponse`` with source
    citations and optional SQL.

    Args:
        request: The user's query packaged as a ``QueryRequest``.

    Returns:
        A ``QueryResponse`` containing the answer, source citations,
        confidence score, and any SQL that was executed.

    Raises:
        GuardrailError: When a guardrail rejects the request or answer.

    Example:
        >>> from lr_bestsellers.models.query import QueryRequest
        >>> response = query(QueryRequest(text="Top segments by cookie reach?"))
    """
    from lr_bestsellers.agent.graph import build_node_context, run_query
    from lr_bestsellers.config import get_settings

    ctx = build_node_context(get_settings())
    return run_query(request.text, ctx)


def ingest() -> None:
    """Trigger a full re-ingestion of all data sources into Qdrant.

    Re-embeds and upserts all ``knowledge_base/`` Markdown files, the
    BigQuery segment catalog, and the glossary into their respective Qdrant
    collections.

    Raises:
        IngestionError: When a source or Qdrant upsert fails.

    Example:
        >>> ingest()
    """
    from lr_bestsellers.__main__ import run_refresh
    from lr_bestsellers.config import get_settings

    run_refresh(get_settings())
