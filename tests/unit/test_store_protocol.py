"""Unit tests for ``VectorStoreProtocol`` using an in-memory fake.

No network I/O — the fake implements the same method signatures as
``QdrantRepository`` so callers can depend on the protocol in isolation.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from lr_bestsellers.models.chunk import SearchResult
from lr_bestsellers.store.protocols import (
    COLLECTION_DOMAIN_KNOWLEDGE,
    COLLECTION_GLOSSARY,
    EMBEDDING_DIM,
    HybridSearchRequest,
    UpsertRecord,
    VectorStoreProtocol,
)
from lr_bestsellers.store.qdrant import cosine_similarity, reciprocal_rank_fusion, record_to_chunk
from lr_bestsellers.store.sparse import sparse_dot, text_to_sparse


def _vec(seed: float) -> list[float]:
    """Build a 768-dim vector dominated by ``seed`` in the first component.

    Args:
        seed: Value placed at index 0; remaining dims are a tiny constant.

    Returns:
        Dense vector of length ``EMBEDDING_DIM``.
    """
    return [seed] + [0.01] * (EMBEDDING_DIM - 1)


def _record(point_id: str, text: str, seed: float) -> UpsertRecord:
    """Build a minimal upsert record for tests.

    Args:
        point_id: External id.
        text: Body text.
        seed: Dense-vector seed.

    Returns:
        Valid ``UpsertRecord``.
    """
    return UpsertRecord(
        point_id=point_id,
        text=text,
        dense_vector=_vec(seed),
        filename="fixture.md",
        section="Overview",
        token_count=max(1, len(text.split())),
    )


class FakeVectorStore:
    """In-memory ``VectorStoreProtocol`` used to prove protocol conformance."""

    def __init__(self) -> None:
        """Initialise empty collection maps."""
        self._points: dict[str, dict[str, UpsertRecord]] = {}

    def upsert(self, collection: str, records: list[UpsertRecord]) -> int:
        """Insert or replace records in an in-memory collection.

        Args:
            collection: Collection name.
            records: Points to store.

        Returns:
            Number of records upserted.
        """
        bucket = self._points.setdefault(collection, {})
        for record in records:
            bucket[record.point_id] = record
        return len(records)

    def hybrid_search(self, request: HybridSearchRequest) -> list[SearchResult]:
        """Rank stored points with dense cosine + sparse overlap, then RRF.

        Args:
            request: Search parameters.

        Returns:
            Top-k ``SearchResult`` list.
        """
        bucket = self._points.get(request.collection, {})
        if not bucket:
            return []
        query_sparse = text_to_sparse(request.query_text)
        dense_ranked: list[tuple[float, str]] = []
        sparse_ranked: list[tuple[float, str]] = []
        for pid, rec in bucket.items():
            dense_ranked.append((cosine_similarity(request.dense_vector, rec.dense_vector), pid))
            sparse_ranked.append((sparse_dot(query_sparse, text_to_sparse(rec.text)), pid))
        dense_ranked.sort(key=lambda item: item[0], reverse=True)
        sparse_ranked.sort(key=lambda item: item[0], reverse=True)
        fused = reciprocal_rank_fusion(
            [pid for _, pid in dense_ranked],
            [pid for _, pid in sparse_ranked],
        )
        ordered = sorted(fused.items(), key=lambda item: item[1], reverse=True)[: request.top_k]
        results: list[SearchResult] = []
        for pid, score in ordered:
            rec = bucket[pid]
            results.append(
                SearchResult(
                    chunk=record_to_chunk(rec),
                    score=min(1.0, score),
                    collection=request.collection,
                    parent_text=rec.parent_text,
                )
            )
        return results

    def delete(self, collection: str, ids: list[str]) -> int:
        """Remove points by id.

        Args:
            collection: Collection name.
            ids: External ids to drop.

        Returns:
            Count of ids that were present and removed.
        """
        bucket = self._points.get(collection, {})
        removed = 0
        for pid in ids:
            if pid in bucket:
                del bucket[pid]
                removed += 1
        return removed

    def collection_exists(self, collection: str) -> bool:
        """Return whether the in-memory collection has been touched.

        Args:
            collection: Collection name.

        Returns:
            ``True`` if upsert created the collection key.
        """
        return collection in self._points


class TestProtocolConformance:
    """``FakeVectorStore`` must be usable wherever ``VectorStoreProtocol`` is."""

    def test_isinstance_runtime_checkable(self) -> None:
        """FakeVectorStore satisfies the runtime-checkable protocol."""
        store: VectorStoreProtocol = FakeVectorStore()
        assert isinstance(store, VectorStoreProtocol)

    def test_upsert_then_exists(self) -> None:
        """Upsert creates a collection that then reports exists=True."""
        store: VectorStoreProtocol = FakeVectorStore()
        assert store.collection_exists(COLLECTION_GLOSSARY) is False
        n = store.upsert(COLLECTION_GLOSSARY, [_record("g1", "activation definition", 0.9)])
        assert n == 1
        assert store.collection_exists(COLLECTION_GLOSSARY) is True

    def test_hybrid_search_returns_search_result(self) -> None:
        """Hybrid search returns typed SearchResult hits."""
        store: VectorStoreProtocol = FakeVectorStore()
        store.upsert(
            COLLECTION_DOMAIN_KNOWLEDGE,
            [
                _record("a", "cookie reach measures cookies", 0.9),
                _record("b", "unrelated platform digest text", 0.1),
            ],
        )
        hits = store.hybrid_search(
            HybridSearchRequest(
                collection=COLLECTION_DOMAIN_KNOWLEDGE,
                query_text="cookie reach",
                dense_vector=_vec(0.9),
                top_k=2,
            )
        )
        assert hits
        assert isinstance(hits[0], SearchResult)
        assert hits[0].collection == COLLECTION_DOMAIN_KNOWLEDGE
        assert hits[0].chunk.chunk_id in {"a", "b"}

    def test_delete_removes_point(self) -> None:
        """Deleted ids no longer appear in search results."""
        store: VectorStoreProtocol = FakeVectorStore()
        store.upsert(COLLECTION_GLOSSARY, [_record("gone", "ssa digest mapping", 0.8)])
        deleted = store.delete(COLLECTION_GLOSSARY, ["gone"])
        assert deleted == 1
        hits = store.hybrid_search(
            HybridSearchRequest(
                collection=COLLECTION_GLOSSARY,
                query_text="ssa digest",
                dense_vector=_vec(0.8),
                top_k=5,
            )
        )
        assert hits == []

    def test_upsert_empty_is_zero(self) -> None:
        """Empty upsert list returns 0."""
        store: VectorStoreProtocol = FakeVectorStore()
        assert store.upsert(COLLECTION_GLOSSARY, []) == 0

    def test_top_k_respected(self) -> None:
        """Search returns at most top_k results."""
        store: VectorStoreProtocol = FakeVectorStore()
        records = [_record(f"p{i}", f"segment reach {i}", 0.5 + i * 0.01) for i in range(5)]
        store.upsert(COLLECTION_DOMAIN_KNOWLEDGE, records)
        hits = store.hybrid_search(
            HybridSearchRequest(
                collection=COLLECTION_DOMAIN_KNOWLEDGE,
                query_text="segment reach",
                dense_vector=_vec(0.55),
                top_k=2,
            )
        )
        assert len(hits) <= 2


class TestUpsertRecordValidation:
    """Pydantic validation on store DTOs."""

    def test_empty_point_id_rejected(self) -> None:
        """Empty point_id fails validation."""
        with pytest.raises(ValidationError):
            UpsertRecord(point_id="", text="x", dense_vector=_vec(0.1))

    def test_hybrid_request_top_k_bounds(self) -> None:
        """top_k=0 is rejected."""
        with pytest.raises(ValidationError):
            HybridSearchRequest(
                collection="glossary",
                query_text="hello",
                dense_vector=_vec(0.1),
                top_k=0,
            )
