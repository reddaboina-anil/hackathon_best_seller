"""Qdrant-backed platform name resolver for Text2SQL hint injection.

At query time, :class:`PlatformResolver` extracts a platform candidate from
the user query with a regex, performs a sparse BM25 search against the
``platform_names`` Qdrant collection (seeded at refresh time from BigQuery),
and returns the canonical platform name to inject into the SQL prompt.

This approach handles:
- Space/punctuation variants (``tradedesk`` → ``The Trade Desk``)
- Partial substrings (``dv360`` → ``Google DV360``)
- Abbreviations with no token overlap (``ttd`` → ``None``, triggers retry)
"""

from __future__ import annotations

import re
from typing import Final

import structlog

from lr_bestsellers.store.protocols import (
    COLLECTION_PLATFORM_NAMES,
    HybridSearchRequest,
    VectorStoreProtocol,
)
from lr_bestsellers.utils.embeddings import EmbedderProtocol

log = structlog.get_logger(__name__)

# Trigger phrases that precede a platform name in natural-language queries.
_TRIGGER_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:activated?\s+(?:to|for)|for\s+platform|on\s+platform|"
    r"to\s+platform|actived?\s+(?:to|for)|on|to|for)\s+([^,.?!\n]+)",
    re.IGNORECASE,
)

# Maximum characters to keep from the captured group.
_MAX_CANDIDATE_LEN: Final[int] = 64


def extract_platform_candidate(query: str) -> str | None:
    """Extract a raw platform-name candidate from a user query.

    Uses :data:`_TRIGGER_RE` to find text following activation-related trigger
    phrases. Returns the first non-empty match, stripped of trailing
    stopwords and punctuation.

    Args:
        query: Raw user question.

    Returns:
        Candidate string (e.g. ``"tradedesk"``, ``"the trade desk"``) or
        ``None`` when no trigger phrase is found.

    Example:
        >>> extract_platform_candidate("top segments activated to tradedesk")
        'tradedesk'
        >>> extract_platform_candidate("what are the best segments?") is None
        True
    """
    match = _TRIGGER_RE.search(query)
    if not match:
        return None
    raw = match.group(1).strip()
    # Trim trailing noise words that aren't part of a platform name.
    noise_re = re.compile(
        r"\s+(?:in\s+the\s+last|last|over|past|during|for|and|or|with|by|at)\b.*$",
        re.IGNORECASE,
    )
    raw = noise_re.sub("", raw).strip()
    if not raw:
        return None
    return raw[:_MAX_CANDIDATE_LEN]


class PlatformResolver:
    """Resolve a raw platform candidate to a canonical BQ value via Qdrant.

    Uses sparse-only BM25 search (no dense embedding needed for short
    strings) against the ``platform_names`` collection. When the collection
    does not exist (e.g. first run before ``refresh --source platform_names``),
    the resolver degrades gracefully and returns ``None``.

    Args:
        store: Vector store repository.
        embedder: Dense embedder (used to supply an empty dense vector for
            the ``HybridSearchRequest`` — sparse score dominates).
    """

    def __init__(self, store: VectorStoreProtocol, embedder: EmbedderProtocol) -> None:
        """Store collaborators.

        Args:
            store: Qdrant-backed or fake vector store.
            embedder: Dense embedder (provides the zero dense query vector).
        """
        self._store = store
        self._embedder = embedder

    def resolve(self, query: str, threshold: float = 0.5) -> str | None:
        """Return the canonical platform name for the query, or ``None``.

        Steps:
        1. :func:`extract_platform_candidate` — regex over trigger phrases.
        2. Sparse BM25 search in ``platform_names`` collection, top-1.
        3. Score ≥ ``threshold`` → return ``payload["text"]`` (canonical name).
        4. Collection missing / score below threshold → return ``None``.

        Args:
            query: User question.
            threshold: Minimum BM25 score to accept a hit (default ``0.5``).

        Returns:
            Canonical platform name string (e.g. ``"The Trade Desk"``) or
            ``None`` when no confident match is found.

        Example:
            >>> resolver.resolve("top segments activated to tradedesk")
            'The Trade Desk'
            >>> resolver.resolve("top segments by cookie reach") is None
            True
        """
        candidate = extract_platform_candidate(query)
        if not candidate:
            log.debug("platform_resolver.no_candidate", query=query[:60])
            return None

        log.debug("platform_resolver.candidate", candidate=candidate)

        try:
            if not self._store.collection_exists(COLLECTION_PLATFORM_NAMES):
                log.warning(
                    "platform_resolver.collection_missing",
                    collection=COLLECTION_PLATFORM_NAMES,
                    note="Run 'refresh --source platform_names' to seed the collection",
                )
                return None
        except Exception as exc:
            log.warning("platform_resolver.existence_check_failed", error=str(exc))
            return None

        try:
            dense_vector = self._embedder.embed_query(candidate)
            hits = self._store.hybrid_search(
                HybridSearchRequest(
                    collection=COLLECTION_PLATFORM_NAMES,
                    query_text=candidate,
                    dense_vector=dense_vector,
                    top_k=1,
                )
            )
        except Exception as exc:
            log.warning("platform_resolver.search_failed", error=str(exc))
            return None

        if not hits:
            log.debug("platform_resolver.no_hits", candidate=candidate)
            return None

        top = hits[0]
        if top.score < threshold:
            log.debug(
                "platform_resolver.below_threshold",
                candidate=candidate,
                score=top.score,
                threshold=threshold,
            )
            return None

        canonical = top.chunk.text.strip()
        log.info(
            "platform_resolver.resolved",
            candidate=candidate,
            canonical=canonical,
            score=top.score,
        )
        return canonical
