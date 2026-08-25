"""Pydantic models for parent-child chunk hierarchy and search results.

The ingestion pipeline splits ``knowledge_base/*.md`` files into a two-level
hierarchy:

- **ParentChunk** — a full H2 section (~1 000–2 000 tokens).  Stored only in
  payload; never indexed directly.
- **ChildChunk** — a ~300-token slice of its parent, prefixed with a
  ``[Doc: … | Section: … | Subsection: …]`` header.  Both dense and sparse
  vectors are stored for hybrid search.

``SearchResult`` wraps a ``ChildChunk`` returned by Qdrant together with its
score and the parent text that will be sent to the LLM for synthesis.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ChildChunk(BaseModel):
    """A small (~300-token) chunk used as the unit of vector search.

    Each child is prefixed at ingest time with a structured header so that the
    LLM always knows its provenance::

        [Doc: activation.md | Section: Delivery Modes | Subsection: FULL]
        <chunk text>

    Attributes:
        chunk_id: Unique identifier (used as Qdrant point ID).
        parent_id: ID of the ``ParentChunk`` this child was split from.
        text: Prefixed chunk text sent to the embedding model.
        embedding: Dense embedding vector (768-dim); ``None`` before ingestion.
        filename: Source file in ``knowledge_base/``.
        section: H2 heading of the parent section.
        subsection: H3 heading, if present.
        token_count: Approximate token count of ``text``.

    Example:
        >>> ChildChunk(
        ...     chunk_id="act_0_0",
        ...     parent_id="act_0",
        ...     text="[Doc: activation.md | Section: Overview] Activation is...",
        ...     filename="activation.md",
        ...     section="Overview",
        ...     token_count=280,
        ... )
    """

    chunk_id: str = Field(..., description="Unique chunk identifier (Qdrant point ID).")
    parent_id: str = Field(..., description="ID of the parent section this chunk belongs to.")
    text: str = Field(..., description="Prefixed chunk text submitted to the embedding model.")
    embedding: list[float] | None = Field(
        None,
        description="Dense embedding vector (768-dim). None before ingest.",
    )
    filename: str = Field(..., description="Source file in knowledge_base/.")
    section: str = Field(..., description="H2 heading of the parent section.")
    subsection: str | None = Field(None, description="H3 heading, if present.")
    token_count: int = Field(..., ge=1, description="Approximate token count of text.")


class ParentChunk(BaseModel):
    """A full H2 section that acts as the retrieval context sent to the LLM.

    The parent is **never** indexed in Qdrant directly; it is stored as a
    payload field on its child chunks and surfaced in ``SearchResult`` so the
    LLM receives rich context rather than just the small child snippet.

    Attributes:
        parent_id: Unique identifier matching ``ChildChunk.parent_id``.
        text: Full section text (not prefixed).
        filename: Source file in ``knowledge_base/``.
        section: H2 heading.
        children: Child chunks produced from this parent.

    Example:
        >>> ParentChunk(
        ...     parent_id="act_0",
        ...     text="Activation is the process of...",
        ...     filename="activation.md",
        ...     section="Overview",
        ... )
    """

    parent_id: str = Field(..., description="Unique parent identifier.")
    text: str = Field(..., description="Full section text (not prefixed, not indexed).")
    filename: str = Field(..., description="Source file in knowledge_base/.")
    section: str = Field(..., description="H2 heading.")
    children: list[ChildChunk] = Field(
        default_factory=list,
        description="Child chunks produced from this parent section.",
    )


class SearchResult(BaseModel):
    """A single result returned by a Qdrant hybrid search.

    The ``parent_text`` field is populated by the retrieval layer from the
    Qdrant payload so that the LLM receives the full section context rather
    than just the matched child snippet.

    Attributes:
        chunk: The matched ``ChildChunk``.
        score: Reciprocal Rank Fusion score from hybrid search.
        collection: Qdrant collection name (``segment_catalog``, ``domain_knowledge``, ``glossary``).
        parent_text: Full parent section text fetched from payload, or ``None``.

    Example:
        >>> SearchResult(
        ...     chunk=ChildChunk(...),
        ...     score=0.87,
        ...     collection="domain_knowledge",
        ... )
    """

    chunk: ChildChunk = Field(..., description="The matched child chunk.")
    score: float = Field(..., ge=0.0, le=1.0, description="RRF relevance score.")
    collection: str = Field(..., description="Qdrant collection the result came from.")
    parent_text: str | None = Field(
        None,
        description="Full parent section text for LLM synthesis context.",
    )
