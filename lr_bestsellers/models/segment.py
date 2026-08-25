"""Pydantic model for a syndicated segment document stored in Qdrant.

One ``SegmentDocument`` corresponds to a single row produced by
``best_sellers.sql``.  Numeric metrics (reach, delivery stats) are *never*
stored here — they are always queried live from BigQuery so that the catalog
reflects the current source of truth.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SegmentDocument(BaseModel):
    r"""A syndicated segment from the LiveRamp segment catalog.

    This is the canonical document type ingested into the ``segment_catalog``
    Qdrant collection.  The text field used for embedding is constructed at
    ingest time as::

        f"Segment: {name}\\nID: {dms_segment_id}\\nDescription: {description}\\nType: Syndicated"

    Attributes:
        dms_segment_id: Unique LiveRamp segment identifier (used as Qdrant point ID).
        seller_customer_id: LiveRamp seller customer ID (used as filter payload).
        name: Human-readable segment name.
        description: Segment description text (the primary semantic content).
        segment_type: Always ``"syndicated"`` — discriminator for multi-type collections.

    Example:
        >>> SegmentDocument(
        ...     dms_segment_id="12345",
        ...     seller_customer_id="seller_abc",
        ...     name="Auto Intenders - New Vehicle",
        ...     description="Users who have shown intent to purchase a new vehicle.",
        ... )
    """

    dms_segment_id: str = Field(
        ...,
        description="Unique LiveRamp segment identifier.",
    )
    seller_customer_id: str = Field(
        ...,
        description="LiveRamp seller customer ID for filtering.",
    )
    name: str = Field(
        ...,
        description="Human-readable segment name.",
    )
    description: str = Field(
        ...,
        description="Segment description used as the primary semantic content.",
    )
    segment_type: Literal["syndicated"] = Field(
        "syndicated",
        description="Segment type discriminator — always 'syndicated'.",
    )

    def to_embedding_text(self) -> str:
        r"""Build the canonical text string used for embedding this document.

        Returns:
            A multi-line string combining segment name, ID, description, and type.

        Example:
            >>> doc = SegmentDocument(
            ...     dms_segment_id="1", seller_customer_id="s1",
            ...     name="Auto Intenders", description="Intent to buy."
            ... )
            >>> doc.to_embedding_text()
            'Segment: Auto Intenders\\nID: 1\\nDescription: Intent to buy.\\nType: Syndicated'
        """
        return (
            f"Segment: {self.name}\n"
            f"ID: {self.dms_segment_id}\n"
            f"Description: {self.description}\n"
            f"Type: Syndicated"
        )
