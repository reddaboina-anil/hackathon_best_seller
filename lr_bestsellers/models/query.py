"""Pydantic models for user queries and agent responses.

This module defines the public I/O contract of the system.  Every entry point
(``query()``, the FastAPI endpoint, CLI) accepts a ``QueryRequest`` and returns
a ``QueryResponse``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

QueryIntent = Literal["analytics", "conceptual", "lookup", "mixed", "vague"]
"""Classified intent of an incoming query.

Values:
    analytics:   Requires aggregation / comparison over numeric segment data.
    conceptual:  Open-ended "what is / how does" question answered by RAG.
    lookup:      Direct field lookup (e.g. "what is the reach of segment X?").
    mixed:       Requires both RAG context and live SQL data.
    vague:       Ambiguous; agent attempts best-effort routing with disclaimer.
"""


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class SqlRow(BaseModel):
    """One BigQuery result row with JSON-serialisable scalar values.

    Attributes:
        fields: Column name to value mapping from a SELECT result.
    """

    fields: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class BqQueryRequest(BaseModel):
    """A SQL statement to execute or dry-run against BigQuery.

    Attributes:
        sql: Standard SQL text.
    """

    sql: str = Field(..., min_length=1)


class SourceCitation(BaseModel):
    """A single cited evidence fragment attached to a ``QueryResponse``.

    Attributes:
        source: Human-readable origin label, e.g. ``"activation.md"``
            or ``"BigQuery:best_sellers"``.
        text: The verbatim chunk text or SQL result row that was cited.
        score: Relevance score in [0, 1].  SQL results use ``1.0``.

    Example:
        >>> SourceCitation(source="activation.md", text="...", score=0.87)
        SourceCitation(source='activation.md', text='...', score=0.87)
    """

    source: str = Field(..., description="Origin label for the cited fragment.")
    text: str = Field(..., description="Verbatim cited text or SQL result row.")
    score: float = Field(..., ge=0.0, le=1.0, description="Relevance score.")


class QueryRequest(BaseModel):
    """A user question submitted to the segment intelligence system.

    Attributes:
        text: Plain-English question, 1–2 000 characters.
        max_results: Maximum chunks to retrieve before re-ranking.
        similarity_threshold: Per-request override for the similarity gate.
        caller_id: Opaque caller identifier used by the rate-limit guardrail.

    Example:
        >>> QueryRequest(text="What are the top segments by cookie reach on TTD?")
        QueryRequest(text='What are the top segments by cookie reach on TTD?', ...)
    """

    text: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="Plain-English question from the user.",
    )
    max_results: int = Field(
        10,
        ge=1,
        le=100,
        description="Maximum chunks to retrieve before re-ranking.",
    )
    similarity_threshold: float = Field(
        0.65,
        ge=0.0,
        le=1.0,
        description="Minimum cosine similarity for the threshold gate.",
    )
    caller_id: str = Field(
        "default",
        description="Opaque caller identifier for rate-limit tracking.",
    )


class QueryResponse(BaseModel):
    """Grounded, cited answer returned by the segment intelligence agent.

    Attributes:
        answer: Final synthesised answer with inline ``[Source: ...]`` markers.
        sources: Evidence fragments cited in the answer.
        sql_used: BigQuery SQL that was executed, if any.
        confidence: Agent self-assessed confidence in [0, 1].
        intent: Classified intent that drove routing decisions.

    Example:
        >>> QueryResponse(
        ...     answer="The top segment is X [Source: BigQuery].",
        ...     sources=[SourceCitation(source="BigQuery", text="...", score=1.0)],
        ...     confidence=0.92,
        ...     intent="analytics",
        ... )
    """

    answer: str = Field(..., description="Synthesised answer with inline citations.")
    sources: list[SourceCitation] = Field(
        default_factory=list,
        description="Evidence fragments cited in the answer.",
    )
    sql_used: str | None = Field(
        None,
        description="BigQuery SQL executed during this request, or None.",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Agent self-assessed confidence score.",
    )
    intent: QueryIntent = Field(
        ...,
        description="Classified intent that determined routing.",
    )
    sql_results: list[SqlRow] = Field(
        default_factory=list,
        description="Raw BigQuery rows returned during this request.",
    )
