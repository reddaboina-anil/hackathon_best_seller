"""Pydantic models for the offline segment catalog served over HTTP.

One :class:`SegmentFeatureRow` corresponds to a single row of
``csv_dump/best_sellers_output.csv`` — a dump of ``best_sellers.sql``.
The API returns either a :class:`CatalogPage` (no ``query`` supplied) or an
:class:`AgentAnswer` (``query`` supplied), discriminated by the ``mode`` field.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

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
        A list of platform names, or ``value`` unchanged when it is not a
        string. ``None`` becomes an empty list.
    """
    if value is None:
        return []
    if not isinstance(value, str):
        return value
    return [name.strip() for name in value.split(PLATFORM_NAME_SEPARATOR) if name.strip()]


def _blank_to_none(value: object) -> object:
    """Turn empty CSV cells into ``None``.

    Args:
        value: Raw CSV cell value.

    Returns:
        ``None`` for empty or whitespace-only strings, else ``value``.
    """
    if isinstance(value, str) and not value.strip():
        return None
    return value


class SegmentFeatureRow(BaseModel):
    """One ``best_sellers.sql`` dump row.

    Known columns are modelled explicitly. Extra columns from a wider export
    are ignored so the contract stays stable.

    Attributes:
        dms_segment_id: Unique LiveRamp segment identifier.
        segment_name: Human-readable taxonomy path of the segment.
        segment_description: Free-text description; ``None`` when the dump is blank.
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
    cookie_reach: int | float | None = Field(None, description="Estimated cookie reach.")
    ios_reach: int | float | None = Field(None, description="Estimated iOS device reach.")
    android_reach: int | float | None = Field(None, description="Estimated Android device reach.")
    input_records: int | float | None = Field(None, description="Input record count.")
    cookie_reach_updated_at: str | None = Field(None, description="Cookie reach as-of timestamp.")
    ios_reach_updated_at: str | None = Field(None, description="iOS reach as-of timestamp.")
    android_reach_updated_at: str | None = Field(None, description="Android reach as-of timestamp.")
    reach_by_platform: str | None = Field(None, description="Per-platform reach labels.")
    distribution_rank: int | None = Field(None, description="Rank by distribution footprint.")
    reach_rank: int | None = Field(None, description="Rank by reach.")
    is_highly_distributed: bool | None = Field(
        None, description="Top decile by destination accounts."
    )
    is_highly_reachable: bool | None = Field(None, description="Top decile by reach.")
    is_top_n_by_reach: bool | None = Field(None, description="Top-N by reach.")

    @field_validator("segment_description", mode="before")
    @classmethod
    def _blank_description_is_null(cls, value: object) -> object:
        """Normalise blank CSV cells to ``None``.

        Args:
            value: Raw CSV cell value.

        Returns:
            ``None`` for empty or whitespace-only strings, else ``value``.
        """
        return _blank_to_none(value)

    @field_validator("active_platform_names", mode="before")
    @classmethod
    def _parse_platform_names(cls, value: object) -> object:
        """Split the aggregated platform-name column into a list.

        Args:
            value: Raw CSV cell value, or an already-parsed list.

        Returns:
            A list of platform names.
        """
        return _split_platform_names(value)

    @field_validator(
        "seller_customer_id",
        "active_destination_accounts",
        "active_buyers",
        "active_platforms",
        "cookie_reach",
        "ios_reach",
        "android_reach",
        "input_records",
        "distribution_rank",
        "reach_rank",
        "is_highly_distributed",
        "is_highly_reachable",
        "is_top_n_by_reach",
        "cookie_reach_updated_at",
        "ios_reach_updated_at",
        "android_reach_updated_at",
        "reach_by_platform",
        mode="before",
    )
    @classmethod
    def _blank_optional_is_null(cls, value: object) -> object:
        """Normalise empty optional cells to ``None``.

        Args:
            value: Raw CSV cell value.

        Returns:
            ``None`` for empty or whitespace-only strings, else ``value``.
        """
        return _blank_to_none(value)

    @field_validator(
        "cookie_reach_updated_at",
        "ios_reach_updated_at",
        "android_reach_updated_at",
        mode="before",
    )
    @classmethod
    def _stringify_timestamp(cls, value: object) -> object:
        """Normalise dump timestamps to ISO-8601 strings.

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


class HealthResponse(BaseModel):
    """Response body for ``GET /v1/health``.

    Attributes:
        status: Always ``"ok"`` when the process is healthy.
        version: API version string, e.g. ``"1.0.0"``.

    Example:
        >>> HealthResponse(status="ok", version="1.0.0")
        HealthResponse(status='ok', version='1.0.0')
    """

    status: str = Field(..., description="Health status — 'ok' when the process is healthy.")
    version: str = Field(..., description="API version string.")
