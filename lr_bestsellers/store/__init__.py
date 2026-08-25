"""Vector store adapters (Qdrant) and the ``VectorStoreProtocol`` interface."""

from __future__ import annotations

from lr_bestsellers.store.protocols import (
    COLLECTION_DOMAIN_KNOWLEDGE,
    COLLECTION_GLOSSARY,
    COLLECTION_SEGMENT_CATALOG,
    COLLECTIONS,
    DENSE_VECTOR_NAME,
    EMBEDDING_DIM,
    SPARSE_VECTOR_NAME,
    HybridSearchRequest,
    UpsertRecord,
    VectorStoreProtocol,
)
from lr_bestsellers.store.qdrant import QdrantRepository

__all__ = [
    "COLLECTION_DOMAIN_KNOWLEDGE",
    "COLLECTION_GLOSSARY",
    "COLLECTION_SEGMENT_CATALOG",
    "COLLECTIONS",
    "DENSE_VECTOR_NAME",
    "EMBEDDING_DIM",
    "SPARSE_VECTOR_NAME",
    "HybridSearchRequest",
    "QdrantRepository",
    "UpsertRecord",
    "VectorStoreProtocol",
]
