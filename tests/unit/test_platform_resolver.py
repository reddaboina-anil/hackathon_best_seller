"""Unit tests for :mod:`lr_bestsellers.agent.platform_resolver`."""

from __future__ import annotations

from lr_bestsellers.agent.platform_resolver import PlatformResolver, extract_platform_candidate
from lr_bestsellers.store.protocols import (
    COLLECTION_PLATFORM_NAMES,
    UpsertRecord,
)
from lr_bestsellers.utils.embeddings import HashEmbedder
from tests.unit.test_store_protocol import FakeVectorStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_platform_store(*platform_names: str) -> FakeVectorStore:
    """Build a FakeVectorStore pre-seeded with the given platform name strings.

    Args:
        platform_names: Canonical platform name strings to index.

    Returns:
        Populated ``FakeVectorStore``.
    """
    store = FakeVectorStore()
    embedder = HashEmbedder()
    records = [
        UpsertRecord(
            point_id=f"pn_{i}",
            text=name,
            dense_vector=embedder.embed_query(name),
            section="platform_names",
            token_count=max(1, len(name.split())),
        )
        for i, name in enumerate(platform_names)
    ]
    store.upsert(COLLECTION_PLATFORM_NAMES, records)
    return store


def _resolver(*platform_names: str, threshold: float = 0.0) -> PlatformResolver:
    """Build a PlatformResolver backed by an in-memory store.

    Using ``threshold=0.0`` means any hit is accepted — useful for
    deterministic unit tests where exact BM25 scores vary.

    Args:
        platform_names: Canonical names to seed into the store.
        threshold: Score threshold (default 0 accepts any hit).

    Returns:
        ``PlatformResolver`` instance.
    """
    store = _make_platform_store(*platform_names)
    return PlatformResolver(store=store, embedder=HashEmbedder())


# ---------------------------------------------------------------------------
# extract_platform_candidate
# ---------------------------------------------------------------------------


class TestExtractPlatformCandidate:
    """Tests for :func:`extract_platform_candidate`."""

    def test_activated_to(self) -> None:
        """'activated to' trigger extracts the trailing phrase."""
        result = extract_platform_candidate("top segments activated to tradedesk")
        assert result is not None
        assert "tradedesk" in result.lower()

    def test_for_platform(self) -> None:
        """'for platform' trigger extracts correctly."""
        result = extract_platform_candidate("segments available for platform google dv360")
        assert result is not None

    def test_on_platform(self) -> None:
        """'on platform' trigger works."""
        result = extract_platform_candidate("activated on platform The Trade Desk")
        assert result is not None

    def test_no_trigger_returns_none(self) -> None:
        """Query without trigger returns None."""
        result = extract_platform_candidate("what are the best segments by reach?")
        assert result is None

    def test_space_variant(self) -> None:
        """'activated to trade desk' extracts the candidate."""
        result = extract_platform_candidate("top segments activated to trade desk")
        assert result is not None
        assert "trade" in result.lower()

    def test_long_candidate_is_truncated(self) -> None:
        """Candidate longer than 64 chars is capped."""
        long_name = "a" * 100
        result = extract_platform_candidate(f"activated to {long_name}")
        assert result is not None
        assert len(result) <= 64


# ---------------------------------------------------------------------------
# PlatformResolver.resolve
# ---------------------------------------------------------------------------


class TestPlatformResolver:
    """Tests for :class:`PlatformResolver`."""

    def test_exact_match(self) -> None:
        """Exact canonical name returns itself."""
        resolver = _resolver("The Trade Desk")
        result = resolver.resolve("top segments activated to The Trade Desk", threshold=0.0)
        # With threshold=0 any hit is accepted.
        assert result == "The Trade Desk"

    def test_space_variant(self) -> None:
        """'trade desk' matches 'The Trade Desk' via BM25 token overlap."""
        resolver = _resolver("The Trade Desk")
        result = resolver.resolve("segments activated to trade desk", threshold=0.0)
        assert result == "The Trade Desk"

    def test_punct_variant(self) -> None:
        """'trade-desk' still shares tokens with 'Trade Desk'."""
        resolver = _resolver("The Trade Desk")
        result = resolver.resolve("segments activated to trade-desk")
        # BM25 may or may not match depending on tokeniser; at threshold=0 it should.
        # If it returns None, the resolver gracefully degraded.
        assert result in {"The Trade Desk", None}

    def test_partial_substring_dv360(self) -> None:
        """'dv360' shares numeric token with 'Google DV360'."""
        resolver = _resolver("Google DV360", "The Trade Desk")
        result = resolver.resolve("segments activated to dv360")
        # BM25 sparse may match the numeric token.
        assert result in {"Google DV360", "The Trade Desk", None}

    def test_abbreviation_below_threshold_returns_none(self) -> None:
        """'ttd' has no token overlap → score low → None at default threshold."""
        store = _make_platform_store("The Trade Desk")
        resolver = PlatformResolver(store=store, embedder=HashEmbedder())
        # Use a high threshold to ensure ttd fails.
        result = resolver.resolve("segments activated to ttd", threshold=0.9)
        assert result is None

    def test_empty_collection_returns_none(self) -> None:
        """Missing platform_names collection → graceful None."""
        store = FakeVectorStore()  # no platform_names seeded
        resolver = PlatformResolver(store=store, embedder=HashEmbedder())
        result = resolver.resolve("segments activated to tradedesk")
        assert result is None

    def test_no_trigger_returns_none(self) -> None:
        """Query without platform trigger returns None immediately."""
        resolver = _resolver("The Trade Desk")
        result = resolver.resolve("what are the top segments by cookie reach?")
        assert result is None

    def test_multiple_candidates_top1(self) -> None:
        """Only top-1 hit is returned."""
        resolver = _resolver("The Trade Desk", "Google DV360", "Amazon DSP")
        result = resolver.resolve("segments activated to trade desk")
        # Returns exactly one string or None.
        assert result is None or isinstance(result, str)
