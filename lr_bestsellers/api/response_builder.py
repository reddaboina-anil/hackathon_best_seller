"""Transform a raw ``QueryResponse`` into a structured ``SegmentQueryResponse``.

The agent pipeline returns answers as a prose string with inline
``[Source: ...]`` markers plus a list of ``SqlRow`` objects.  This module
shapes those two outputs into a clean ``SegmentQueryResponse`` where every
matched segment is a typed ``SegmentResult`` — suitable for programmatic
consumption without parsing markdown.

Priority order for building the segment list:

1. **SQL results** (``QueryResponse.sql_results``) — fully structured; field
   names are mapped to ``SegmentResult`` via a well-known-key lookup table.
2. **Vector-search citations** (``QueryResponse.sources``) — used as fallback
   when no SQL was executed or returned zero rows.  Citations whose ``source``
   is ``"BigQuery"`` are skipped in the fallback path because they were already
   accounted for in path 1.

Deduplication is performed on ``dms_segment_id`` (case-insensitive string
comparison).  Segments without an ID are deduplicated on ``segment_name``.
"""

from __future__ import annotations

import structlog

from lr_bestsellers.models.query import (
    QueryResponse,
    SegmentQueryResponse,
    SegmentResult,
    SourceCitation,
)

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Field-name aliases
# Keys are lower-cased column names that BigQuery / the LLM may return.
# ---------------------------------------------------------------------------

_NAME_KEYS: frozenset[str] = frozenset(
    {"segment_name", "name", "segment_label", "segment_title"}
)
_ID_KEYS: frozenset[str] = frozenset(
    {"dms_segment_id", "segment_id", "id", "dms_id"}
)
_DESC_KEYS: frozenset[str] = frozenset(
    {"segment_description", "description", "desc", "segment_desc"}
)
_DIST_RANK_KEYS: frozenset[str] = frozenset({"distribution_rank", "dist_rank"})
_IMP_RANK_KEYS: frozenset[str] = frozenset({"impressions_rank", "impression_rank"})
_PROV_REV_RANK_KEYS: frozenset[str] = frozenset(
    {"provider_revenue_rank", "provider_rev_rank", "prov_rev_rank"}
)
_BUYER_RANK_KEYS: frozenset[str] = frozenset({"buyer_usage_rank", "buyer_rank"})
_PLATFORM_RANK_KEYS: frozenset[str] = frozenset(
    {"platform_usage_rank", "platform_rank"}
)
_PLATFORM_NAMES_KEYS: frozenset[str] = frozenset(
    {"active_platform_names", "platform_names", "platforms"}
)

_PLATFORM_NAME_SEPARATORS: tuple[str, ...] = (", ", ",", "|", ";")


def _pick(
    fields: dict[str, str | int | float | bool | None],
    candidates: frozenset[str],
) -> str | int | float | bool | None:
    """Return the first value in ``fields`` whose key is in ``candidates``.

    The lookup is case-insensitive.

    Args:
        fields: Column name to value mapping from a ``SqlRow``.
        candidates: Set of lower-cased column name aliases to try.

    Returns:
        The matched value, or ``None`` when no candidate key is present.
    """
    for key, value in fields.items():
        if key.lower() in candidates:
            return value
    return None


def _to_str(value: str | int | float | bool | None) -> str | None:
    """Coerce a scalar to ``str``, returning ``None`` for blank/null values.

    Args:
        value: Raw field value from a ``SqlRow``.

    Returns:
        String representation, or ``None`` for ``None`` / blank strings.
    """
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _to_int(value: str | int | float | bool | None) -> int | None:
    """Coerce a scalar to ``int``, returning ``None`` on failure.

    Args:
        value: Raw field value from a ``SqlRow``.

    Returns:
        Integer, or ``None`` when the value cannot be converted.
    """
    if value is None:
        return None
    try:
        return int(float(str(value)))
    except (ValueError, TypeError):
        return None


def _parse_platform_names(value: str | int | float | bool | None) -> list[str]:
    """Split a platform-name aggregate string into a list.

    BigQuery's ``STRING_AGG`` joins platform names with ``", "``.  This
    function handles that separator and common alternatives.

    Args:
        value: Raw field value — may be a pre-joined string or any scalar.

    Returns:
        List of stripped platform name strings.
    """
    if value is None:
        return []
    text = str(value).strip()
    if not text:
        return []
    for sep in _PLATFORM_NAME_SEPARATORS:
        if sep in text:
            return [p.strip() for p in text.split(sep) if p.strip()]
    return [text]


def _segment_from_sql_row(
    rank: int,
    fields: dict[str, str | int | float | bool | None],
) -> SegmentResult | None:
    """Build a ``SegmentResult`` from one BigQuery result row.

    Args:
        rank: 1-based result position.
        fields: Column name to value mapping from a ``SqlRow``.

    Returns:
        A ``SegmentResult``, or ``None`` when the row has no segment name.
    """
    name_raw = _pick(fields, _NAME_KEYS)
    segment_name = _to_str(name_raw)
    if not segment_name:
        log.warning("response_builder.skip_row", reason="no_segment_name", rank=rank)
        return None

    return SegmentResult(
        rank=rank,
        dms_segment_id=_to_str(_pick(fields, _ID_KEYS)),
        segment_name=segment_name,
        description=_to_str(_pick(fields, _DESC_KEYS)),
        distribution_rank=_to_int(_pick(fields, _DIST_RANK_KEYS)),
        impressions_rank=_to_int(_pick(fields, _IMP_RANK_KEYS)),
        provider_revenue_rank=_to_int(_pick(fields, _PROV_REV_RANK_KEYS)),
        buyer_usage_rank=_to_int(_pick(fields, _BUYER_RANK_KEYS)),
        platform_usage_rank=_to_int(_pick(fields, _PLATFORM_RANK_KEYS)),
        active_platform_names=_parse_platform_names(_pick(fields, _PLATFORM_NAMES_KEYS)),
        source="BigQuery",
        relevance_score=None,
    )


def _segment_from_citation(rank: int, citation: SourceCitation) -> SegmentResult | None:
    """Build a ``SegmentResult`` from a vector-search ``SourceCitation``.

    The citation ``text`` field is free-form.  We extract what we can from it
    and fall back to the source label for the segment name when nothing better
    is available.

    Args:
        rank: 1-based result position.
        citation: A ``SourceCitation`` from the vector-search path.

    Returns:
        A ``SegmentResult``, or ``None`` for BigQuery citations (already
        handled by the SQL path).
    """
    if citation.source.lower().startswith("bigquery"):
        return None

    text = citation.text.strip()
    # Try to extract a segment name from the first non-empty line.
    first_line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    segment_name = first_line[:200] if first_line else citation.source

    return SegmentResult(
        rank=rank,
        dms_segment_id=None,
        segment_name=segment_name,
        description=text[:500] if text else None,
        distribution_rank=None,
        impressions_rank=None,
        provider_revenue_rank=None,
        buyer_usage_rank=None,
        platform_usage_rank=None,
        active_platform_names=[],
        source="VectorSearch",
        relevance_score=round(citation.score, 4),
    )


def _dedup_segments(segments: list[SegmentResult]) -> list[SegmentResult]:
    """Remove duplicate segments, preserving the first occurrence.

    Deduplication key: ``dms_segment_id`` (case-insensitive) when present,
    otherwise the lower-cased ``segment_name``.

    Args:
        segments: Ordered list of candidate ``SegmentResult`` objects.

    Returns:
        Deduplicated list with original ordering maintained.
    """
    seen: set[str] = set()
    out: list[SegmentResult] = []
    for seg in segments:
        key = (
            seg.dms_segment_id.lower()
            if seg.dms_segment_id
            else seg.segment_name.lower()
        )
        if key not in seen:
            seen.add(key)
            out.append(seg)
    return out


def build_segment_query_response(
    query: str,
    response: QueryResponse,
    elapsed_ms: int | None = None,
) -> SegmentQueryResponse:
    """Shape a raw ``QueryResponse`` into a structured ``SegmentQueryResponse``.

    Segment objects are built first from SQL rows (``response.sql_results``).
    When that list is empty, vector-search citations are used as a fallback,
    skipping any citation whose source starts with ``"BigQuery"``.  Duplicates
    are removed before final ranking is assigned.

    Args:
        query: The original user question (echoed in the response envelope).
        response: Raw pipeline output from ``service.answer_query()``.
        elapsed_ms: Wall-clock milliseconds taken by the pipeline call, or
            ``None`` when timing was not captured.

    Returns:
        A fully populated ``SegmentQueryResponse``.

    Example:
        >>> from lr_bestsellers.models.query import QueryResponse
        >>> raw = QueryResponse(
        ...     answer="...", confidence=0.9, intent="analytics", sql_results=[]
        ... )
        >>> result = build_segment_query_response("my query", raw, elapsed_ms=500)
        >>> result.query
        'my query'
    """
    segments: list[SegmentResult] = []

    if response.sql_results:
        log.info(
            "response_builder.sql_path",
            rows=len(response.sql_results),
        )
        for i, row in enumerate(response.sql_results, start=1):
            seg = _segment_from_sql_row(i, row.fields)
            if seg is not None:
                segments.append(seg)
    else:
        log.info(
            "response_builder.vector_fallback",
            citations=len(response.sources),
        )
        rank = 1
        for citation in response.sources:
            seg = _segment_from_citation(rank, citation)
            if seg is not None:
                segments.append(seg)
                rank += 1

    segments = _dedup_segments(segments)

    # Re-assign rank after deduplication so it is always contiguous.
    for i, seg in enumerate(segments, start=1):
        segments[i - 1] = seg.model_copy(update={"rank": i})

    log.info(
        "response_builder.complete",
        total_found=len(segments),
        source="BigQuery" if response.sql_results else "VectorSearch",
    )

    return SegmentQueryResponse(
        query=query,
        intent=response.intent,
        confidence=response.confidence,
        total_found=len(segments),
        segments=segments,
        narrative=response.answer,
        sql_used=response.sql_used,
        citations=response.sources,
        processing_time_ms=elapsed_ms,
    )
