"""Shared catalog row → :class:`RawDocument` mapping for BQ and CSV ingest."""

from __future__ import annotations

from collections.abc import Mapping

from lr_bestsellers.exceptions import IngestionError
from lr_bestsellers.ingestion.protocols import RawDocument
from lr_bestsellers.models.segment import SegmentDocument
from lr_bestsellers.utils.chunking import count_tokens


def document_from_catalog_row(
    row: Mapping[str, object],
    collection: str,
    *,
    filename: str,
) -> RawDocument:
    """Map a catalog row (SQL or CSV) to a :class:`RawDocument`.

    Args:
        row: Mapping with ``dms_segment_id``, ``seller_customer_id``,
            ``segment_name``, ``segment_description``.
        collection: Target Qdrant collection name.
        filename: Source label stored on the document.

    Returns:
        Document ready to embed.

    Raises:
        IngestionError: When ``dms_segment_id`` is missing or empty.
    """
    dms_id = str(row.get("dms_segment_id") or "").strip()
    if not dms_id:
        raise IngestionError("Catalog row is missing dms_segment_id")
    doc = _row_to_segment(row)
    text = doc.to_embedding_text()
    return RawDocument(
        point_id=doc.dms_segment_id,
        text=text,
        collection=collection,
        parent_text=text,
        filename=filename,
        section=doc.name,
        parent_id=doc.dms_segment_id,
        token_count=count_tokens(text),
        dms_segment_id=doc.dms_segment_id,
        seller_customer_id=doc.seller_customer_id,
    )


def _row_to_segment(row: Mapping[str, object]) -> SegmentDocument:
    """Map a catalog row to a :class:`SegmentDocument`.

    Args:
        row: Mapping-like query or CSV row.

    Returns:
        Catalog document (metrics discarded).
    """
    dms_id = str(row.get("dms_segment_id") or "").strip()
    seller = str(row.get("seller_customer_id") or "").strip()
    name = str(row.get("segment_name") or "").strip()
    description = str(row.get("segment_description") or "").strip()
    return SegmentDocument(
        dms_segment_id=dms_id,
        seller_customer_id=seller or "unknown",
        name=name or dms_id,
        description=description or name or dms_id,
    )
