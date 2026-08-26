"""Pydantic models for the standalone tag API.

Public contracts only — callers never see DuckDB rows as bare dicts.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

PLATFORM_NAME_SEPARATOR: Final[str] = ", "
"""Separator used by the ``STRING_AGG`` calls that build the platform columns."""

TagCategory = Literal["platform", "reach", "distribution"]
"""Category of a recommendation tag."""

DEFAULT_PAGE_SIZE: Final[int] = 50
"""Number of segment IDs returned when ``size`` is omitted."""

MAX_PAGE_SIZE: Final[int] = 200
"""Largest number of segment IDs returned in a single page."""


class TagDefinition(BaseModel):
    """A recommendation tag that can be attached to a segment.

    Attributes:
        tag_key: Stable slug used in URLs and in the DuckDB store.
        display_name: Human-readable badge label.
        description: Short explanation of how the tag is computed.
        category: One of ``platform``, ``reach``, or ``distribution``.
        priority: Display order; lower numbers surface first.

    Example:
        >>> TagDefinition(
        ...     tag_key="high_ios_reach",
        ...     display_name="High iOS Reach",
        ...     description="Top 10% by iOS device reach",
        ...     category="reach",
        ...     priority=5,
        ... ).tag_key
        'high_ios_reach'
    """

    model_config = ConfigDict(extra="forbid")

    tag_key: str = Field(..., min_length=1, description="Stable slug used in URLs.")
    display_name: str = Field(..., min_length=1, description="Human-readable badge label.")
    description: str = Field(..., min_length=1, description="How the tag is computed.")
    category: TagCategory = Field(..., description="Tag category.")
    priority: int = Field(..., ge=1, description="Display order, lower first.")


def _split_platform_names(value: object) -> object:
    """Split a ``", "``-joined aggregate string into a list of names.

    Args:
        value: Raw dump cell value, or an already-parsed list.

    Returns:
        A list of platform names, or ``value`` unchanged when it is not a
        string. ``None`` becomes an empty list.
    """
    if value is None:
        return []
    if not isinstance(value, str):
        return value
    return [name.strip() for name in value.split(PLATFORM_NAME_SEPARATOR) if name.strip()]


class SegmentRow(BaseModel):
    """One ``segment_dump`` row plus the tags assigned to that segment.

    Known enriched-export columns are modelled explicitly. Extra columns
    from a wider export are ignored so the contract stays stable.

    Attributes:
        dms_segment_id: Unique LiveRamp segment identifier.
        tags: Tag definitions assigned to this segment; empty when none apply.
    """

    model_config = ConfigDict(extra="ignore")

    dms_segment_id: int = Field(..., description="Unique LiveRamp segment identifier.")
    segment_name: str | None = Field(None, description="Segment taxonomy path.")
    segment_description: str | None = Field(None, description="Segment description text.")
    segment_type: str | None = Field(None, description="Marketplace segment type.")
    seller_customer_id: int | None = Field(None, description="Selling data provider customer ID.")
    active_destination_accounts: int | None = Field(
        None, description="Enabled destination accounts."
    )
    active_buyers: int | None = Field(None, description="Distinct buyers with the segment enabled.")
    active_platforms: int | None = Field(
        None, description="Distinct platforms distributing the segment."
    )
    active_platform_names: list[str] = Field(
        default_factory=list,
        description="Platforms the segment is currently distributed to.",
    )
    buyers_with_usage: int | None = Field(None, description="Buyers with actual segment usage.")
    platforms_with_usage: int | None = Field(
        None, description="Platforms with actual segment usage."
    )
    impressions: float | None = Field(None, description="Total impression count.")
    gross_data_revenue: float | None = Field(None, description="Gross data revenue.")
    provider_net_revenue: float | None = Field(None, description="Provider net revenue.")
    liveramp_net_revenue: float | None = Field(None, description="LiveRamp net revenue.")
    cookie_reach: int | float | None = Field(None, description="Estimated cookie reach.")
    ios_reach: int | float | None = Field(None, description="Estimated iOS device reach.")
    android_reach: int | float | None = Field(None, description="Estimated Android device reach.")
    max_connect_reach: float | None = Field(None, description="Max Connect reach.")
    input_records: int | float | None = Field(None, description="Input record count.")
    reach_by_platform: str | None = Field(None, description="Per-platform reach labels.")
    cookie_reach_updated_at: str | None = Field(None, description="Cookie reach as-of timestamp.")
    ios_reach_updated_at: str | None = Field(None, description="iOS reach as-of timestamp.")
    android_reach_updated_at: str | None = Field(None, description="Android reach as-of timestamp.")
    distribution_rank: int | None = Field(None, description="Rank by distribution footprint.")
    impressions_rank: int | None = Field(None, description="Rank by impressions.")
    provider_revenue_rank: int | None = Field(None, description="Rank by provider revenue.")
    buyer_usage_rank: int | None = Field(None, description="Rank by buyer usage.")
    platform_usage_rank: int | None = Field(None, description="Rank by platform usage.")
    reach_rank: int | None = Field(None, description="Rank by reach.")
    popularity_score: float | None = Field(None, description="Computed popularity score.")
    popularity_rank: int | None = Field(None, description="Rank by popularity score.")
    is_highly_distributed: bool | None = Field(
        None, description="Top decile by destination accounts."
    )
    is_highly_used: bool | None = Field(None, description="Top decile by usage.")
    is_highly_reachable: bool | None = Field(None, description="Top decile by reach.")
    is_top_n_popular: bool | None = Field(None, description="Top-N by popularity score.")
    tags: list[TagDefinition] = Field(
        default_factory=list,
        description="Tags assigned to this segment, ordered by priority.",
    )

    @field_validator("segment_description", mode="before")
    @classmethod
    def _blank_description_is_null(cls, value: object) -> object:
        """Normalise blank cells to ``None``.

        Args:
            value: Raw dump cell value.

        Returns:
            ``None`` for empty or whitespace-only strings, else ``value``.
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("active_platform_names", mode="before")
    @classmethod
    def _parse_platform_names(cls, value: object) -> object:
        """Split the aggregated platform-name column into a list.

        Args:
            value: Raw dump cell value, or an already-parsed list.

        Returns:
            A list of platform names.
        """
        return _split_platform_names(value)

    @field_validator(
        "seller_customer_id",
        "active_destination_accounts",
        "active_buyers",
        "active_platforms",
        "buyers_with_usage",
        "platforms_with_usage",
        "impressions",
        "gross_data_revenue",
        "provider_net_revenue",
        "liveramp_net_revenue",
        "cookie_reach",
        "ios_reach",
        "android_reach",
        "max_connect_reach",
        "input_records",
        "reach_by_platform",
        "distribution_rank",
        "impressions_rank",
        "provider_revenue_rank",
        "buyer_usage_rank",
        "platform_usage_rank",
        "reach_rank",
        "popularity_score",
        "popularity_rank",
        "is_highly_distributed",
        "is_highly_used",
        "is_highly_reachable",
        "is_top_n_popular",
        mode="before",
    )
    @classmethod
    def _blank_optional_is_null(cls, value: object) -> object:
        """Normalise empty optional cells to ``None``.

        Args:
            value: Raw dump cell value.

        Returns:
            ``None`` for empty or whitespace-only strings, else ``value``.
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator(
        "cookie_reach_updated_at",
        "ios_reach_updated_at",
        "android_reach_updated_at",
        mode="before",
    )
    @classmethod
    def _stringify_timestamp(cls, value: object) -> object:
        """Normalise DuckDB timestamps to ISO-8601 strings.

        Args:
            value: Raw cell value.

        Returns:
            An ISO-8601 string for date/datetime values, else ``value``.
        """
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, date):
            return value.isoformat()
        return value


class SegmentTag(BaseModel):
    """A tag assignment for a single segment.

    Attributes:
        segment_id: LiveRamp ``dms_segment_id``.
        tag_key: Slug of the assigned tag.
        score: Assignment strength in ``[0, 1]``. Pre-computed tags use ``1.0``.

    Example:
        >>> SegmentTag(segment_id=1015151361, tag_key="high_ios_reach").score
        1.0
    """

    model_config = ConfigDict(extra="forbid")

    segment_id: int = Field(..., description="LiveRamp dms_segment_id.")
    tag_key: str = Field(..., min_length=1, description="Assigned tag slug.")
    score: float = Field(1.0, ge=0.0, le=1.0, description="Assignment strength.")


class PageInfo(BaseModel):
    """Pagination metadata describing a returned window of segment IDs.

    Attributes:
        page: 1-based page number that was returned.
        page_size: Requested rows per page.
        total_items: Total matching segment IDs.
        total_pages: Total number of pages at this ``page_size``.
        has_next: Whether a following page exists.
        has_previous: Whether a preceding page exists.
    """

    model_config = ConfigDict(extra="forbid")

    page: int = Field(..., ge=1, description="1-based page number returned.")
    page_size: int = Field(..., ge=1, description="Requested rows per page.")
    total_items: int = Field(..., ge=0, description="Total matching segment IDs.")
    total_pages: int = Field(..., ge=0, description="Total pages at this page size.")
    has_next: bool = Field(..., description="Whether a following page exists.")
    has_previous: bool = Field(..., description="Whether a preceding page exists.")

    @classmethod
    def from_total(cls, page: int, page_size: int, total_items: int) -> PageInfo:
        """Build pagination metadata from a total count.

        Args:
            page: 1-based page number.
            page_size: Requested rows per page.
            total_items: Total matching rows.

        Returns:
            A ``PageInfo`` with derived ``total_pages`` and next/previous flags.
        """
        total_pages = math.ceil(total_items / page_size) if page_size > 0 else 0
        return cls(
            page=page,
            page_size=page_size,
            total_items=total_items,
            total_pages=total_pages,
            has_next=page < total_pages,
            has_previous=page > 1,
        )


class SegmentsPage(BaseModel):
    """One page of dump rows with their assigned tags.

    Attributes:
        pagination: Metadata describing the returned window.
        items: Dump rows in ``dms_segment_id`` order, each with tags.
    """

    model_config = ConfigDict(extra="forbid")

    pagination: PageInfo = Field(..., description="Pagination metadata.")
    items: list[SegmentRow] = Field(
        default_factory=list,
        description="Dump rows for the requested page, each with assigned tags.",
    )


class TagsPage(BaseModel):
    """One page of segment IDs that carry a given tag.

    Attributes:
        tag_key: Slug whose members are being listed.
        pagination: Metadata describing the returned window.
        items: Segment IDs on this page, ordered by score descending.
    """

    model_config = ConfigDict(extra="forbid")

    tag_key: str = Field(..., min_length=1, description="Tag slug being listed.")
    pagination: PageInfo = Field(..., description="Pagination metadata.")
    items: list[int] = Field(default_factory=list, description="Segment IDs on this page.")


class ErrorResponse(BaseModel):
    """Error envelope returned for every non-2xx response.

    Attributes:
        error: Machine-readable error code, e.g. ``TAG_NOT_FOUND``.
        detail: Human-readable explanation safe to show to callers.
    """

    model_config = ConfigDict(extra="forbid")

    error: str = Field(..., description="Machine-readable error code.")
    detail: str = Field(..., description="Human-readable explanation.")


class HealthStatus(BaseModel):
    """Liveness payload for ``GET /healthz``.

    Attributes:
        status: Always ``ok`` when the process is serving requests.
    """

    model_config = ConfigDict(extra="forbid")

    status: str = Field("ok", description="Process liveness marker.")
