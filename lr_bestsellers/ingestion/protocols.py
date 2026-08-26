"""Ingestion source protocol and shared raw-document DTO."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import structlog
from pydantic import BaseModel, Field

from lr_bestsellers.store.protocols import UpsertRecord
from lr_bestsellers.utils.embeddings import EmbedderProtocol

log = structlog.get_logger(__name__)


class RawDocument(BaseModel):
    """A document ready to embed, before a dense vector exists.

    Attributes:
        point_id: External Qdrant point id.
        text: Text that will be embedded and sparsified.
        collection: Target collection name.
        parent_text: Parent section for LLM context.
        filename: Source file if any.
        section: H2 or term heading.
        subsection: Optional H3.
        parent_id: Parent id.
        token_count: Approximate tokens in ``text``.
        dms_segment_id: Catalog id.
        seller_customer_id: Seller id.
    """

    point_id: str = Field(..., min_length=1)
    text: str = Field(..., min_length=1)
    collection: str = Field(..., min_length=1)
    parent_text: str | None = None
    filename: str | None = None
    section: str = "unknown"
    subsection: str | None = None
    parent_id: str | None = None
    token_count: int = Field(1, ge=1)
    dms_segment_id: str | None = None
    seller_customer_id: str | None = None


@runtime_checkable
class IngestionSourceProtocol(Protocol):
    """A pull-based source that yields ``RawDocument`` rows."""

    @property
    def name(self) -> str:
        """Stable source name (``files``, ``bq``, ``glossary``)."""
        ...

    @property
    def collection(self) -> str:
        """Qdrant collection this source writes to."""
        ...

    def load(self) -> list[RawDocument]:
        """Load documents from the backing system.

        Returns:
            Documents without dense embeddings.

        Raises:
            IngestionError: When the source cannot be read.
        """
        ...


def documents_to_records(
    documents: list[RawDocument],
    vectors: list[list[float]],
) -> list[UpsertRecord]:
    """Zip raw documents with embeddings into upsert records.

    Args:
        documents: Source documents.
        vectors: Dense embeddings aligned with ``documents``.

    Returns:
        Upsert records.

    Raises:
        ValueError: When lengths differ.
    """
    if len(documents) != len(vectors):
        raise ValueError("documents and vectors length mismatch")
    records: list[UpsertRecord] = []
    for doc, vector in zip(documents, vectors, strict=True):
        records.append(
            UpsertRecord(
                point_id=doc.point_id,
                text=doc.text,
                dense_vector=vector,
                parent_text=doc.parent_text,
                filename=doc.filename,
                section=doc.section,
                subsection=doc.subsection,
                parent_id=doc.parent_id,
                token_count=doc.token_count,
                dms_segment_id=doc.dms_segment_id,
                seller_customer_id=doc.seller_customer_id,
            )
        )
    return records


def embed_and_upsert(
    source: IngestionSourceProtocol,
    embedder: EmbedderProtocol,
    store: object,
) -> int:
    """Load, embed, and upsert one ingestion source.

    Args:
        source: Document source.
        embedder: Dense embedder.
        store: Object exposing ``upsert(collection, records) -> int``.

    Returns:
        Number of points upserted.
    """
    pager = getattr(source, "iter_pages", None)
    if callable(pager):
        total = 0
        row_offset: int = getattr(source, "offset", 0)
        upsert = getattr(store, "upsert")
        for page_index, documents in enumerate(pager()):
            if not documents:
                continue
            vectors = embedder.embed_documents([doc.text for doc in documents])
            records = documents_to_records(documents, vectors)
            n = int(upsert(source.collection, records))
            total += n
            log.info(
                "ingest.page_upserted",
                source=source.name,
                page=page_index,
                upserted=n,
                total=total,
                total_absolute=row_offset + total,
            )
        return total
    documents = source.load()
    if not documents:
        return 0
    vectors = embedder.embed_documents([doc.text for doc in documents])
    records = documents_to_records(documents, vectors)
    upsert = getattr(store, "upsert")
    return int(upsert(source.collection, records))
