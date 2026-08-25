"""Live Qdrant integration tests (skipped when Qdrant is not reachable)."""

from __future__ import annotations

import pytest
from qdrant_client import QdrantClient
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse

from lr_bestsellers.store.protocols import (
    COLLECTION_GLOSSARY,
    EMBEDDING_DIM,
    HybridSearchRequest,
    UpsertRecord,
)
from lr_bestsellers.store.qdrant import QdrantRepository

_TEST_COLLECTION = "itest_glossary"


def _qdrant_available() -> bool:
    """Return True if a local Qdrant instance answers on localhost:6333.

    Returns:
        ``True`` when ``collection_exists`` succeeds against localhost.
    """
    client = QdrantClient(url="http://localhost:6333", timeout=2.0)
    try:
        client.collection_exists(collection_name=_TEST_COLLECTION)
    except (UnexpectedResponse, ResponseHandlingException, OSError, TimeoutError):
        return False
    return True


@pytest.fixture
def repo() -> QdrantRepository:
    """QdrantRepository bound to local Docker Qdrant.

    Yields:
        Repository with a throwaway collection created and later deleted.
    """
    client = QdrantClient(url="http://localhost:6333")
    repository = QdrantRepository(client)
    if repository.collection_exists(_TEST_COLLECTION):
        client.delete_collection(collection_name=_TEST_COLLECTION)
    # Reuse production schema via a one-off create through ensure by temporarily
    # upserting after manual create matching QdrantRepository.ensure_collections.
    from qdrant_client.models import (
        Distance,
        SparseIndexParams,
        SparseVectorParams,
        VectorParams,
    )

    from lr_bestsellers.store.protocols import DENSE_VECTOR_NAME, SPARSE_VECTOR_NAME

    client.create_collection(
        collection_name=_TEST_COLLECTION,
        vectors_config={
            DENSE_VECTOR_NAME: VectorParams(size=EMBEDDING_DIM, distance=Distance.COSINE),
        },
        sparse_vectors_config={
            SPARSE_VECTOR_NAME: SparseVectorParams(index=SparseIndexParams(on_disk=False)),
        },
    )
    yield repository
    if repository.collection_exists(_TEST_COLLECTION):
        client.delete_collection(collection_name=_TEST_COLLECTION)


@pytest.mark.skipif(not _qdrant_available(), reason="Qdrant is not running on localhost:6333")
def test_upsert_search_delete_roundtrip(repo: QdrantRepository) -> None:
    """Upsert a point, find it via hybrid search, then delete it."""
    dense = [0.0] * EMBEDDING_DIM
    dense[0] = 1.0
    record = UpsertRecord(
        point_id="itest-1",
        text="cookie reach is the estimated cookie audience",
        dense_vector=dense,
        filename="reach_metrics.md",
        section="cookie_reach",
        token_count=8,
    )
    assert repo.upsert(_TEST_COLLECTION, [record]) == 1
    hits = repo.hybrid_search(
        HybridSearchRequest(
            collection=_TEST_COLLECTION,
            query_text="cookie reach audience",
            dense_vector=dense,
            top_k=5,
        )
    )
    assert any(h.chunk.chunk_id == "itest-1" for h in hits)
    assert repo.delete(_TEST_COLLECTION, ["itest-1"]) == 1
    assert repo.collection_exists(_TEST_COLLECTION) is True
    assert COLLECTION_GLOSSARY  # referenced so the import stays meaningful
