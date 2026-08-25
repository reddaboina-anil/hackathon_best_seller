"""Protocols and Pydantic DTOs for the storage repositories.

``VectorStoreProtocol`` is the injection boundary for Qdrant (and fakes used
in unit tests). Callers pass :class:`UpsertRecord` / :class:`HybridSearchRequest`
Pydantic models — never bare dicts.

``CatalogRepositoryProtocol`` is the injection boundary for the offline segment
catalog served by the API's browse branch.
"""

from __future__ import annotations

from typing import Final, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from lr_bestsellers.models.catalog import CatalogPage, PageRequest
from lr_bestsellers.models.chunk import SearchResult

EMBEDDING_DIM: Final[int] = 768
"""Dense embedding dimensionality (``gemini-embedding-2`` Matryoshka 768)."""

DENSE_VECTOR_NAME: Final[str] = "dense"
"""Named dense vector in Qdrant collections."""

SPARSE_VECTOR_NAME: Final[str] = "sparse"
"""Named sparse (BM25-style) vector in Qdrant collections."""

COLLECTION_SEGMENT_CATALOG: Final[str] = "segment_catalog"
COLLECTION_DOMAIN_KNOWLEDGE: Final[str] = "domain_knowledge"
COLLECTION_GLOSSARY: Final[str] = "glossary"

COLLECTIONS: Final[tuple[str, str, str]] = (
    COLLECTION_SEGMENT_CATALOG,
    COLLECTION_DOMAIN_KNOWLEDGE,
    COLLECTION_GLOSSARY,
)


class UpsertRecord(BaseModel):
    """A single point to upsert into a Qdrant collection.

    Attributes:
        point_id: Stable external identifier stored in payload (Qdrant uses a UUID).
        text: Source text used to build the sparse vector.
        dense_vector: 768-dim dense embedding.
        parent_text: Parent section text for LLM context (not indexed).
        filename: Source markdown filename, if applicable.
        section: H2 heading, if applicable.
        subsection: H3 heading, if applicable.
        parent_id: Parent chunk id, if applicable.
        token_count: Approximate token count of ``text``.
        dms_segment_id: Segment catalog id (filter payload only).
        seller_customer_id: Seller id (filter payload only).
    """

    point_id: str = Field(..., min_length=1, description="External point identifier.")
    text: str = Field(..., min_length=1, description="Text used for sparse encoding.")
    dense_vector: list[float] = Field(..., description="768-dim dense embedding.")
    parent_text: str | None = Field(None, description="Parent section text for synthesis.")
    filename: str | None = Field(None, description="Source filename in knowledge_base/.")
    section: str = Field("unknown", description="H2 heading or catalog label.")
    subsection: str | None = Field(None, description="H3 heading, if present.")
    parent_id: str | None = Field(None, description="Parent chunk identifier.")
    token_count: int = Field(1, ge=1, description="Approximate token count.")
    dms_segment_id: str | None = Field(None, description="Segment id for catalog points.")
    seller_customer_id: str | None = Field(None, description="Seller id for catalog points.")


class HybridSearchRequest(BaseModel):
    """Parameters for a hybrid (dense + sparse) search.

    Attributes:
        collection: Target Qdrant collection name.
        query_text: Raw query used to build the sparse vector.
        dense_vector: Query embedding from ``gemini-embedding-2``.
        top_k: Maximum number of fused results to return.
    """

    collection: str = Field(..., min_length=1, description="Qdrant collection name.")
    query_text: str = Field(..., min_length=1, description="User query text.")
    dense_vector: list[float] = Field(..., description="Query dense embedding.")
    top_k: int = Field(10, ge=1, le=100, description="Number of fused results.")


@runtime_checkable
class VectorStoreProtocol(Protocol):
    """Repository interface for upsert, hybrid search, delete, and existence checks.

    Implementations wrap a vector database. Unit tests use an in-memory fake
    that satisfies this protocol.
    """

    def upsert(self, collection: str, records: list[UpsertRecord]) -> int:
        """Insert or update points in ``collection``.

        Args:
            collection: Target collection name.
            records: Points to upsert.

        Returns:
            Number of points upserted.

        Raises:
            RetrievalError: When the underlying store rejects the write.
        """
        ...

    def hybrid_search(self, request: HybridSearchRequest) -> list[SearchResult]:
        """Run dense + sparse search fused with Reciprocal Rank Fusion.

        Args:
            request: Collection, query text, dense vector, and ``top_k``.

        Returns:
            Ranked ``SearchResult`` list (length ≤ ``top_k``).

        Raises:
            RetrievalError: When the underlying store is unreachable.
        """
        ...

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
        ...

    def collection_exists(self, collection: str) -> bool:
        """Return whether ``collection`` exists.

        Args:
            collection: Collection name to probe.

        Returns:
            ``True`` if the collection exists.

        Raises:
            RetrievalError: When the existence check itself fails.
        """
        ...


@runtime_checkable
class CatalogRepositoryProtocol(Protocol):
    """Repository interface for reading pages of the offline segment catalog.

    Implementations wrap a static dump of the BigQuery segment recommendation
    features table. Unit tests use small fixture files or in-memory fakes that
    satisfy this protocol.
    """

    def page(self, request: PageRequest) -> CatalogPage:
        """Return one page of catalog rows.

        Args:
            request: Requested pagination window.

        Returns:
            The requested rows plus pagination metadata. ``items`` is empty when
            the window starts past the end of the catalog.

        Raises:
            CatalogError: When the underlying dump is missing or malformed.
        """
        ...

    def row_count(self) -> int:
        """Return the total number of rows in the catalog.

        Returns:
            Row count, excluding the header.

        Raises:
            CatalogError: When the underlying dump is missing or malformed.
        """
        ...
