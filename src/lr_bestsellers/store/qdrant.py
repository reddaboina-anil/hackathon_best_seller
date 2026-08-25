"""Qdrant implementation of :class:`VectorStoreProtocol`.

Manages the three collections used by lr-bestsellers:

* ``segment_catalog``
* ``domain_knowledge``
* ``glossary``

Each collection stores a named dense (768-dim cosine) vector and a named
sparse BM25-style vector. Hybrid search uses Qdrant's Reciprocal Rank Fusion
when talking to a live server; the same RRF math is used by the in-memory fake.
"""

from __future__ import annotations

import uuid
from typing import Any, Final

import structlog
from qdrant_client import QdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.models import (
    Distance,
    Fusion,
    FusionQuery,
    PointIdsList,
    PointStruct,
    Prefetch,
    SparseIndexParams,
    SparseVectorParams,
    VectorParams,
)
from qdrant_client.models import (
    SparseVector as QdrantSparseVector,
)

from lr_bestsellers.exceptions import RetrievalError
from lr_bestsellers.models.chunk import ChildChunk, SearchResult
from lr_bestsellers.store.protocols import (
    COLLECTIONS,
    DENSE_VECTOR_NAME,
    EMBEDDING_DIM,
    SPARSE_VECTOR_NAME,
    HybridSearchRequest,
    UpsertRecord,
)
from lr_bestsellers.store.sparse import text_to_sparse

log = structlog.get_logger(__name__)

_RRF_K: Final[int] = 60
_UUID_NAMESPACE: Final[uuid.UUID] = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")


def point_uuid(point_id: str) -> str:
    """Derive a stable Qdrant UUID from an external string id.

    Args:
        point_id: External identifier (chunk id or segment id).

    Returns:
        RFC-4122 UUID string.
    """
    return str(uuid.uuid5(_UUID_NAMESPACE, point_id))


def cosine_similarity(left: list[float], right: list[float]) -> float:
    """Cosine similarity clipped to ``[0, 1]`` (negative scores become 0).

    Args:
        left: First dense vector.
        right: Second dense vector.

    Returns:
        Similarity in ``[0.0, 1.0]``. Returns ``0.0`` if either vector is empty
        or has zero magnitude.
    """
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = 0.0
    n_left = 0.0
    n_right = 0.0
    for a, b in zip(left, right, strict=True):
        dot += a * b
        n_left += a * a
        n_right += b * b
    if n_left <= 0.0 or n_right <= 0.0:
        return 0.0
    raw = dot / (n_left**0.5 * n_right**0.5)
    return float(max(0.0, min(1.0, raw)))


def reciprocal_rank_fusion(
    dense_ids: list[str],
    sparse_ids: list[str],
    k: int = _RRF_K,
) -> dict[str, float]:
    """Fuse two ranked id lists with Reciprocal Rank Fusion.

    Args:
        dense_ids: Point ids ordered by dense score (best first).
        sparse_ids: Point ids ordered by sparse score (best first).
        k: RRF smoothing constant.

    Returns:
        Mapping of point id → fused score (already in ``(0, 1]`` for typical k).
    """
    scores: dict[str, float] = {}
    for rank, pid in enumerate(dense_ids, start=1):
        scores[pid] = scores.get(pid, 0.0) + 1.0 / (k + rank)
    for rank, pid in enumerate(sparse_ids, start=1):
        scores[pid] = scores.get(pid, 0.0) + 1.0 / (k + rank)
    return scores


def record_to_chunk(record: UpsertRecord) -> ChildChunk:
    """Convert an upsert record into a ``ChildChunk`` for search results.

    Args:
        record: Stored upsert record.

    Returns:
        Equivalent ``ChildChunk``.
    """
    return ChildChunk(
        chunk_id=record.point_id,
        parent_id=record.parent_id or record.point_id,
        text=record.text,
        embedding=record.dense_vector,
        filename=record.filename or "unknown",
        section=record.section,
        subsection=record.subsection,
        token_count=record.token_count,
    )


class QdrantRepository:
    """Qdrant-backed :class:`VectorStoreProtocol` implementation.

    Args:
        client: Initialised ``QdrantClient`` (injected for testability).
    """

    def __init__(self, client: QdrantClient) -> None:
        """Store the injected Qdrant client.

        Args:
            client: Live or in-memory Qdrant client.
        """
        self._client = client

    def ensure_collections(self) -> None:
        """Create the three standard collections if they do not exist.

        Raises:
            RetrievalError: When collection creation fails.
        """
        for name in COLLECTIONS:
            if self.collection_exists(name):
                continue
            try:
                self._client.create_collection(
                    collection_name=name,
                    vectors_config={
                        DENSE_VECTOR_NAME: VectorParams(
                            size=EMBEDDING_DIM,
                            distance=Distance.COSINE,
                        ),
                    },
                    sparse_vectors_config={
                        SPARSE_VECTOR_NAME: SparseVectorParams(
                            index=SparseIndexParams(on_disk=False),
                        ),
                    },
                )
            except UnexpectedResponse as exc:
                log.error("qdrant.create_collection_failed", collection=name, error=str(exc))
                raise RetrievalError(f"Failed to create collection {name!r}") from exc
            log.info("qdrant.collection_created", collection=name)

    def recreate_collections(self) -> None:
        """Drop and recreate all standard collections.

        Raises:
            RetrievalError: When delete or create fails.
        """
        for name in COLLECTIONS:
            try:
                if self.collection_exists(name):
                    self._client.delete_collection(collection_name=name)
            except UnexpectedResponse as exc:
                log.error("qdrant.delete_collection_failed", collection=name, error=str(exc))
                raise RetrievalError(f"Failed to delete collection {name!r}") from exc
        self.ensure_collections()
        log.info("qdrant.collections_recreated")

    def collection_exists(self, collection: str) -> bool:
        """Return whether ``collection`` exists on the server.

        Args:
            collection: Collection name to probe.

        Returns:
            ``True`` if the collection exists.

        Raises:
            RetrievalError: When the existence check fails for a reason other
                than "not found".
        """
        try:
            exists = self._client.collection_exists(collection_name=collection)
        except UnexpectedResponse as exc:
            log.error("qdrant.collection_exists_failed", collection=collection, error=str(exc))
            raise RetrievalError(f"Qdrant collection_exists failed for {collection!r}") from exc
        return bool(exists)

    def upsert(self, collection: str, records: list[UpsertRecord]) -> int:
        """Insert or update points in ``collection``.

        Args:
            collection: Target collection name.
            records: Points to upsert.

        Returns:
            Number of points upserted.

        Raises:
            RetrievalError: When Qdrant rejects the write.
            ValueError: When a dense vector has the wrong dimension.
        """
        if not records:
            return 0
        points: list[PointStruct] = []
        for record in records:
            if len(record.dense_vector) != EMBEDDING_DIM:
                raise ValueError(
                    f"dense_vector for {record.point_id!r} has length "
                    f"{len(record.dense_vector)}, expected {EMBEDDING_DIM}"
                )
            sparse = text_to_sparse(record.text)
            payload: dict[str, Any] = record.model_dump()
            payload["dense_vector"] = record.dense_vector
            points.append(
                PointStruct(
                    id=point_uuid(record.point_id),
                    vector={
                        DENSE_VECTOR_NAME: record.dense_vector,
                        SPARSE_VECTOR_NAME: QdrantSparseVector(
                            indices=sparse.indices,
                            values=sparse.values,
                        ),
                    },
                    payload=payload,
                )
            )
        try:
            self._client.upsert(collection_name=collection, points=points)
        except UnexpectedResponse as exc:
            log.error("qdrant.upsert_failed", collection=collection, error=str(exc))
            raise RetrievalError(f"Qdrant upsert failed for collection {collection!r}") from exc
        log.info("qdrant.upsert_ok", collection=collection, count=len(records))
        return len(records)

    def hybrid_search(self, request: HybridSearchRequest) -> list[SearchResult]:
        """Run dense + sparse search fused with Reciprocal Rank Fusion.

        Args:
            request: Collection, query text, dense vector, and ``top_k``.

        Returns:
            Ranked ``SearchResult`` list (length ≤ ``top_k``).

        Raises:
            RetrievalError: When Qdrant is unreachable or returns an error.
            ValueError: When the query vector has the wrong dimension.
        """
        if len(request.dense_vector) != EMBEDDING_DIM:
            raise ValueError(
                f"query dense_vector has length {len(request.dense_vector)}, "
                f"expected {EMBEDDING_DIM}"
            )
        sparse = text_to_sparse(request.query_text)
        try:
            response = self._client.query_points(
                collection_name=request.collection,
                prefetch=[
                    Prefetch(
                        query=request.dense_vector,
                        using=DENSE_VECTOR_NAME,
                        limit=request.top_k,
                    ),
                    Prefetch(
                        query=QdrantSparseVector(
                            indices=sparse.indices,
                            values=sparse.values,
                        ),
                        using=SPARSE_VECTOR_NAME,
                        limit=request.top_k,
                    ),
                ],
                query=FusionQuery(fusion=Fusion.RRF),
                limit=request.top_k,
                with_payload=True,
            )
        except UnexpectedResponse as exc:
            log.error(
                "qdrant.search_failed",
                collection=request.collection,
                error=str(exc),
            )
            raise RetrievalError(
                f"Qdrant search failed for collection {request.collection!r}"
            ) from exc

        results: list[SearchResult] = []
        for point in response.points:
            payload = point.payload or {}
            record = _payload_to_record(payload)
            score = float(point.score) if point.score is not None else 0.0
            score = max(0.0, min(1.0, score))
            results.append(
                SearchResult(
                    chunk=record_to_chunk(record),
                    score=score,
                    collection=request.collection,
                    parent_text=record.parent_text,
                )
            )
        log.info(
            "qdrant.hybrid_search_ok",
            collection=request.collection,
            hits=len(results),
        )
        return results

    def delete(self, collection: str, ids: list[str]) -> int:
        """Delete points by external ``point_id``.

        Args:
            collection: Target collection name.
            ids: External identifiers previously passed to :meth:`upsert`.

        Returns:
            Number of points requested for deletion.

        Raises:
            RetrievalError: When the delete call fails.
        """
        if not ids:
            return 0
        qdrant_ids: list[int | str] = [point_uuid(i) for i in ids]
        try:
            self._client.delete(
                collection_name=collection,
                points_selector=PointIdsList(points=list(qdrant_ids)),
            )
        except UnexpectedResponse as exc:
            log.error("qdrant.delete_failed", collection=collection, error=str(exc))
            raise RetrievalError(f"Qdrant delete failed for collection {collection!r}") from exc
        log.info("qdrant.delete_ok", collection=collection, count=len(ids))
        return len(ids)


def _payload_to_record(payload: dict[str, Any]) -> UpsertRecord:
    """Rebuild an ``UpsertRecord`` from a Qdrant payload dict.

    Args:
        payload: Stored payload (may be partial on older points).

    Returns:
        Validated ``UpsertRecord``.
    """
    dense = payload.get("dense_vector") or []
    if not isinstance(dense, list):
        dense = []
    text = payload.get("text") or ""
    point_id = payload.get("point_id") or "unknown"
    return UpsertRecord(
        point_id=str(point_id),
        text=str(text) if text else " ",
        dense_vector=[float(x) for x in dense] if dense else [0.0] * EMBEDDING_DIM,
        parent_text=payload.get("parent_text"),
        filename=payload.get("filename"),
        section=str(payload.get("section") or "unknown"),
        subsection=payload.get("subsection"),
        parent_id=payload.get("parent_id"),
        token_count=int(payload.get("token_count") or 1),
        dms_segment_id=payload.get("dms_segment_id"),
        seller_customer_id=payload.get("seller_customer_id"),
    )
