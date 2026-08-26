"""Pydantic models for user queries and agent responses.

This module defines the public I/O contract of the system.  Every entry point
(``query()``, the FastAPI endpoint, CLI) accepts a ``QueryRequest`` and returns
a ``QueryResponse``.  The structured ``POST /v1/query`` endpoint uses
``QueryTextRequest`` as its body and ``SegmentQueryResponse`` as its reply.
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


# ---------------------------------------------------------------------------
# Structured query API — POST /v1/query
# ---------------------------------------------------------------------------

SegmentSource = Literal["BigQuery", "VectorSearch", "hybrid"]
"""Origin of a :class:`SegmentResult`.

Values:
    BigQuery:     Segment came from a live SQL result row.
    VectorSearch: Segment came from a Qdrant vector-search hit only.
    hybrid:       Segment was found by both paths and merged.
"""


class QueryTextRequest(BaseModel):
    """POST body for the ``POST /v1/query`` endpoint.

    Attributes:
        query: Plain-English question, 1–2 000 characters.
        caller_id: Opaque rate-limit bucket key forwarded to the pipeline.

    Example:
        >>> QueryTextRequest(query="Top segments by cookie reach on TTD?")
        QueryTextRequest(query='Top segments by cookie reach on TTD?', caller_id='api')
    """

    query: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="Plain-English question from the user.",
    )
    caller_id: str = Field(
        "api",
        description="Opaque caller identifier for rate-limit tracking.",
    )


class SegmentResult(BaseModel):
    """One structured segment hit returned by the query endpoint.

    Fields are populated from BigQuery ``SqlRow`` results when available,
    falling back to Qdrant vector-search citations.  All rank fields are
    ``None`` when the segment was retrieved via vector search only.

    Attributes:
        rank: 1-based position in the result list.
        dms_segment_id: Unique LiveRamp segment identifier, or ``None`` when
            not surfaced by the query.
        segment_name: Human-readable taxonomy path of the segment.
        description: Free-text description; ``None`` when absent.
        distribution_rank: Dense rank by distribution footprint (1 = widest).
        impressions_rank: Dense rank by impressions (1 = highest).
        provider_revenue_rank: Dense rank by provider net revenue (1 = highest).
        buyer_usage_rank: Dense rank by buyers with usage (1 = highest).
        platform_usage_rank: Dense rank by platforms with usage (1 = highest).
        active_platform_names: Platforms the segment is currently distributed to.
        source: Whether this hit came from BigQuery, vector search, or both.
        relevance_score: Cosine similarity from vector search; ``None`` for
            pure SQL results.

    Example:
        >>> SegmentResult(
        ...     rank=1,
        ...     segment_name="Experian > Travel > Frequent International Travelers",
        ...     source="BigQuery",
        ... )
    """

    rank: int = Field(..., ge=1, description="1-based result position.")
    dms_segment_id: str | None = Field(
        None, description="Unique LiveRamp segment identifier."
    )
    segment_name: str = Field(..., description="Human-readable segment taxonomy path.")
    description: str | None = Field(None, description="Segment description text.")
    distribution_rank: int | None = Field(
        None, description="Dense rank by distribution footprint (1 = widest)."
    )
    impressions_rank: int | None = Field(
        None, description="Dense rank by impressions (1 = highest)."
    )
    provider_revenue_rank: int | None = Field(
        None, description="Dense rank by provider net revenue (1 = highest)."
    )
    buyer_usage_rank: int | None = Field(
        None, description="Dense rank by buyers with usage (1 = highest)."
    )
    platform_usage_rank: int | None = Field(
        None, description="Dense rank by platforms with usage (1 = highest)."
    )
    active_platform_names: list[str] = Field(
        default_factory=list,
        description="Platforms the segment is currently distributed to.",
    )
    source: SegmentSource = Field(
        ..., description="Data origin: BigQuery, VectorSearch, or hybrid."
    )
    relevance_score: float | None = Field(
        None,
        ge=0.0,
        le=1.0,
        description="Cosine similarity from vector search; None for SQL-only results.",
    )


class SegmentQueryResponse(BaseModel):
    """Structured response envelope for ``POST /v1/query``.

    Unlike the legacy ``AgentAnswer`` which embeds a raw markdown string, this
    model exposes each matched segment as a typed :class:`SegmentResult` object
    suitable for programmatic consumption.

    Attributes:
        query: The original question that was answered.
        intent: Classified routing intent.
        confidence: Agent self-assessed confidence in [0, 1].
        total_found: Number of distinct segments returned.
        segments: Ordered list of matched segment objects.
        narrative: The LLM-generated prose answer (useful for human-facing UIs).
        sql_used: BigQuery SQL that was executed, or ``None``.
        citations: Evidence fragments cited in the answer.
        processing_time_ms: Wall-clock milliseconds for the full pipeline call.

    Example:
        >>> SegmentQueryResponse(
        ...     query="Top travel segments",
        ...     intent="mixed",
        ...     confidence=0.91,
        ...     total_found=3,
        ...     segments=[],
        ...     narrative="Here are ...",
        ...     sql_used=None,
        ...     citations=[],
        ...     processing_time_ms=1200,
        ... )
    """

    query: str = Field(..., description="The original question.")
    intent: QueryIntent = Field(..., description="Classified routing intent.")
    confidence: float = Field(
        ..., ge=0.0, le=1.0, description="Agent self-assessed confidence score."
    )
    total_found: int = Field(..., ge=0, description="Number of distinct segments returned.")
    segments: list[SegmentResult] = Field(
        default_factory=list, description="Ordered list of matched segment objects."
    )
    narrative: str = Field(
        ..., description="LLM-generated prose answer with inline citations."
    )
    sql_used: str | None = Field(
        None, description="BigQuery SQL executed during this request, or None."
    )
    citations: list[SourceCitation] = Field(
        default_factory=list, description="Evidence fragments cited in the answer."
    )
    processing_time_ms: int | None = Field(
        None, description="Wall-clock milliseconds for the full pipeline call."
    )
