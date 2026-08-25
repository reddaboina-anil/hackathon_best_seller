"""Pydantic models for the offline segment catalog served over HTTP.

One :class:`SegmentFeatureRow` corresponds to a single row of
``csv_dump/segment_recommendation_features.csv`` — a dump of the BigQuery
segment recommendation features table.  The API returns either a
:class:`CatalogPage` (no ``query`` supplied) or an :class:`AgentAnswer`
(``query`` supplied), discriminated by the ``mode`` field.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Final, Literal

from pydantic import BaseModel, Field, field_validator

from lr_bestsellers.models.query import QueryResponse

PLATFORM_NAME_SEPARATOR: Final[str] = ", "
"""Separator used by the ``STRING_AGG`` calls that build the platform columns."""

MAX_PAGE_SIZE: Final[int] = 200
"""Largest number of catalog rows returned in a single page."""

DEFAULT_PAGE_SIZE: Final[int] = 50
"""Number of catalog rows returned when ``page_size`` is omitted."""


def _split_platform_names(value: object) -> object:
    """Split a ``", "``-joined aggregate string into a list of names.

    Args:
        value: Raw CSV cell value, or an already-parsed list.

    Returns:
        A list of platform names, or ``value`` unchanged when it is not a string.
    """
    if not isinstance(value, str):
        return value
    return [name.strip() for name in value.split(PLATFORM_NAME_SEPARATOR) if name.strip()]


class SegmentFeatureRow(BaseModel):
    """Distribution, usage, and revenue features for one syndicated segment.

    Attributes:
        dms_segment_id: Unique LiveRamp segment identifier.
        segment_name: Human-readable taxonomy path of the segment.
        segment_description: Free-text description; ``None`` when the dump is blank.
        segment_type: Marketplace segment type (always ``Syndicated`` in this dump).
        seller_customer_id: LiveRamp customer ID of the selling data provider.
        active_platform_names: Platforms the segment is currently distributed to.
        usage_platform_names: Platforms that actually delivered impressions.
        active_destination_accounts: Count of enabled destination accounts.
        active_buyers: Count of distinct buyers with the segment enabled.
        active_distribution_platforms: Count of distinct platforms distributing it.
        buyers_with_usage: Count of buyers that delivered impressions.
        platforms_with_usage: Count of platforms that delivered impressions.
        impressions: Impressions delivered in the usage window.
        gross_data_revenue: Gross data revenue in the usage window.
        provider_net_revenue: Net revenue attributed to the data provider.
        liveramp_net_revenue: Net revenue attributed to LiveRamp.
        distribution_rank: Dense rank by distribution footprint (1 is widest).
        impressions_rank: Dense rank by impressions (1 is highest).
        provider_revenue_rank: Dense rank by provider net revenue (1 is highest).
        buyer_usage_rank: Dense rank by buyers with usage (1 is highest).
        platform_usage_rank: Dense rank by platforms with usage (1 is highest).
        popularity_score: Blended popularity score in [0, 1].
        popularity_rank: Dense rank by ``popularity_score`` (1 is most popular).
        is_highly_distributed: Segment is in the top decile by distribution.
        is_highly_used: Segment is in the top decile by usage.
        is_top_n_popular: Segment is in the top-N popularity cut.
        usage_start_date: First day of the usage measurement window.
        usage_end_date: Last day of the usage measurement window.
    """

    dms_segment_id: int = Field(..., description="Unique LiveRamp segment identifier.")
    segment_name: str = Field(..., description="Segment taxonomy path.")
    segment_description: str | None = Field(None, description="Segment description text.")
    segment_type: str = Field(..., description="Marketplace segment type.")
    seller_customer_id: int = Field(..., description="Selling data provider customer ID.")

    active_platform_names: list[str] = Field(
        default_factory=list,
        description="Platforms the segment is currently distributed to.",
    )
    usage_platform_names: list[str] = Field(
        default_factory=list,
        description="Platforms that delivered impressions for the segment.",
    )

    active_destination_accounts: int = Field(..., ge=0, description="Enabled destination accounts.")
    active_buyers: int = Field(..., ge=0, description="Distinct buyers with the segment enabled.")
    active_distribution_platforms: int = Field(
        ...,
        ge=0,
        description="Distinct platforms distributing the segment.",
    )
    buyers_with_usage: int = Field(..., ge=0, description="Buyers that delivered impressions.")
    platforms_with_usage: int = Field(
        ..., ge=0, description="Platforms that delivered impressions."
    )

    impressions: float = Field(..., description="Impressions delivered in the usage window.")
    gross_data_revenue: float = Field(..., description="Gross data revenue in the usage window.")
    provider_net_revenue: float = Field(..., description="Net revenue for the data provider.")
    liveramp_net_revenue: float = Field(..., description="Net revenue for LiveRamp.")

    distribution_rank: int = Field(..., ge=1, description="Rank by distribution footprint.")
    impressions_rank: int = Field(..., ge=1, description="Rank by impressions.")
    provider_revenue_rank: int = Field(..., ge=1, description="Rank by provider net revenue.")
    buyer_usage_rank: int = Field(..., ge=1, description="Rank by buyers with usage.")
    platform_usage_rank: int = Field(..., ge=1, description="Rank by platforms with usage.")

    popularity_score: float = Field(..., description="Blended popularity score.")
    popularity_rank: int = Field(..., ge=1, description="Rank by popularity score.")

    is_highly_distributed: bool = Field(..., description="In the top decile by distribution.")
    is_highly_used: bool = Field(..., description="In the top decile by usage.")
    is_top_n_popular: bool = Field(..., description="In the top-N popularity cut.")

    usage_start_date: date = Field(..., description="First day of the usage window.")
    usage_end_date: date = Field(..., description="Last day of the usage window.")

    @field_validator("segment_description", mode="before")
    @classmethod
    def _blank_description_is_null(cls, value: object) -> object:
        """Normalise blank CSV cells to ``None``.

        Args:
            value: Raw CSV cell value.

        Returns:
            ``None`` for empty or whitespace-only strings, else ``value``.
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("active_platform_names", "usage_platform_names", mode="before")
    @classmethod
    def _parse_platform_names(cls, value: object) -> object:
        """Split the aggregated platform-name columns into lists.

        Args:
            value: Raw CSV cell value, or an already-parsed list.

        Returns:
            A list of platform names.
        """
        return _split_platform_names(value)


class PageRequest(BaseModel):
    """Pagination window requested by an API caller.

    Attributes:
        page: 1-based page number.
        page_size: Number of rows per page.

    Example:
        >>> PageRequest(page=2, page_size=25).offset
        25
    """

    page: int = Field(1, ge=1, description="1-based page number.")
    page_size: int = Field(
        DEFAULT_PAGE_SIZE,
        ge=1,
        le=MAX_PAGE_SIZE,
        description="Number of rows per page.",
    )

    @property
    def offset(self) -> int:
        """Return the zero-based index of the first row on this page.

        Returns:
            ``(page - 1) * page_size``.
        """
        return (self.page - 1) * self.page_size


class PageInfo(BaseModel):
    """Pagination metadata describing the returned window.

    Attributes:
        page: 1-based page number that was returned.
        page_size: Requested rows per page.
        total_items: Total rows available in the catalog.
        total_pages: Total number of pages at this ``page_size``.
        has_next: Whether a following page exists.
        has_previous: Whether a preceding page exists.
    """

    page: int = Field(..., ge=1, description="1-based page number returned.")
    page_size: int = Field(..., ge=1, description="Requested rows per page.")
    total_items: int = Field(..., ge=0, description="Total rows in the catalog.")
    total_pages: int = Field(..., ge=0, description="Total pages at this page size.")
    has_next: bool = Field(..., description="Whether a following page exists.")
    has_previous: bool = Field(..., description="Whether a preceding page exists.")


class CatalogPage(BaseModel):
    """One page of catalog rows read straight from the CSV dump.

    Attributes:
        mode: Response discriminator — always ``"catalog"``.
        source: Name of the CSV file the rows were read from.
        pagination: Metadata describing the returned window.
        items: Catalog rows in dump order.
    """

    mode: Literal["catalog"] = Field("catalog", description="Response discriminator.")
    source: str = Field(..., description="CSV file the rows were read from.")
    pagination: PageInfo = Field(..., description="Pagination metadata.")
    items: list[SegmentFeatureRow] = Field(
        default_factory=list,
        description="Catalog rows for the requested page.",
    )


class AgentAnswer(BaseModel):
    """A grounded agent answer produced by the RAG + Text2SQL graph.

    Attributes:
        mode: Response discriminator — always ``"agent"``.
        query: The question that was answered.
        result: Full agent response with citations and optional SQL.
    """

    mode: Literal["agent"] = Field("agent", description="Response discriminator.")
    query: str = Field(..., description="Question that was answered.")
    result: QueryResponse = Field(..., description="Grounded, cited agent response.")


SegmentsResult = Annotated[AgentAnswer | CatalogPage, Field(discriminator="mode")]
"""Union returned by the segments endpoint, discriminated on ``mode``."""


class ErrorResponse(BaseModel):
    """Error envelope returned for every non-2xx response.

    Attributes:
        error: Machine-readable error code, e.g. ``PII_DETECTED``.
        detail: Human-readable explanation safe to show to callers.
    """

    error: str = Field(..., description="Machine-readable error code.")
    detail: str = Field(..., description="Human-readable explanation.")
