"""Pydantic validation edge cases for tag-api models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from models import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    ErrorResponse,
    HealthStatus,
    PageInfo,
    SegmentRow,
    SegmentsPage,
    SegmentTag,
    TagDefinition,
    TagsPage,
)


class TestTagDefinition:
    """Validation for ``TagDefinition``."""

    def test_valid(self) -> None:
        """A fully specified definition is accepted."""
        tag = TagDefinition(
            tag_key="high_ios_reach",
            display_name="High iOS Reach",
            description="Top 10% by iOS device reach",
            category="reach",
            priority=5,
        )
        assert tag.tag_key == "high_ios_reach"
        assert tag.category == "reach"

    def test_empty_tag_key_rejected(self) -> None:
        """``tag_key`` must be non-empty."""
        with pytest.raises(ValidationError):
            TagDefinition(
                tag_key="",
                display_name="X",
                description="Y",
                category="reach",
                priority=1,
            )

    def test_unknown_category_rejected(self) -> None:
        """Category must be one of the three literals."""
        with pytest.raises(ValidationError):
            TagDefinition.model_validate(
                {
                    "tag_key": "x",
                    "display_name": "X",
                    "description": "Y",
                    "category": "revenue",
                    "priority": 1,
                }
            )

    def test_priority_must_be_positive(self) -> None:
        """Priority is 1-based."""
        with pytest.raises(ValidationError):
            TagDefinition(
                tag_key="x",
                display_name="X",
                description="Y",
                category="reach",
                priority=0,
            )

    def test_extra_fields_forbidden(self) -> None:
        """Unknown fields are rejected so the contract stays tight."""
        with pytest.raises(ValidationError):
            TagDefinition.model_validate(
                {
                    "tag_key": "x",
                    "display_name": "X",
                    "description": "Y",
                    "category": "reach",
                    "priority": 1,
                    "extra": "nope",
                }
            )


class TestSegmentTag:
    """Validation for ``SegmentTag``."""

    def test_default_score(self) -> None:
        """Pre-computed assignments default to a score of 1.0."""
        assignment = SegmentTag(segment_id=1015151361, tag_key="high_ios_reach")
        assert assignment.score == 1.0

    def test_score_bounds(self) -> None:
        """Score must sit in ``[0, 1]``."""
        SegmentTag(segment_id=1, tag_key="x", score=0.0)
        SegmentTag(segment_id=1, tag_key="x", score=1.0)
        with pytest.raises(ValidationError):
            SegmentTag(segment_id=1, tag_key="x", score=1.1)
        with pytest.raises(ValidationError):
            SegmentTag(segment_id=1, tag_key="x", score=-0.01)


class TestPageInfo:
    """Derived pagination flags."""

    def test_from_total_first_page(self) -> None:
        """Page 1 of 3 has a next page and no previous page."""
        info = PageInfo.from_total(page=1, page_size=10, total_items=25)
        assert info.total_pages == 3
        assert info.has_next is True
        assert info.has_previous is False

    def test_from_total_last_page(self) -> None:
        """The last page has a previous page and no next page."""
        info = PageInfo.from_total(page=3, page_size=10, total_items=25)
        assert info.has_next is False
        assert info.has_previous is True

    def test_from_total_empty(self) -> None:
        """Zero items yields zero pages."""
        info = PageInfo.from_total(page=1, page_size=50, total_items=0)
        assert info.total_pages == 0
        assert info.has_next is False

    def test_page_size_constants(self) -> None:
        """Default and max page sizes match the API contract."""
        assert DEFAULT_PAGE_SIZE == 50
        assert MAX_PAGE_SIZE == 200


class TestTagsPage:
    """``TagsPage`` shape."""

    def test_valid(self) -> None:
        """A page with two segment IDs round-trips."""
        page = TagsPage(
            tag_key="high_ios_reach",
            pagination=PageInfo.from_total(1, 50, 2),
            items=[1001, 1002],
        )
        assert page.items == [1001, 1002]
        dumped = page.model_dump()
        assert dumped["pagination"]["total_items"] == 2


class TestSegmentRow:
    """Dump-row mapping for ``GET /v1/segments``."""

    def test_extra_columns_ignored(self) -> None:
        """Unknown dump columns do not fail validation."""
        row = SegmentRow.model_validate({"dms_segment_id": 1, "unexpected": "x"})
        assert row.dms_segment_id == 1

    def test_blank_description_is_null(self) -> None:
        """Whitespace-only descriptions become ``None``."""
        row = SegmentRow.model_validate({"dms_segment_id": 1, "segment_description": "  "})
        assert row.segment_description is None

    def test_platform_names_split(self) -> None:
        """A joined platform string is split on ``, ``."""
        row = SegmentRow.model_validate(
            {"dms_segment_id": 1, "active_platform_names": "Facebook, The Trade Desk"}
        )
        assert row.active_platform_names == ["Facebook", "The Trade Desk"]

    def test_timestamp_from_datetime(self) -> None:
        """DuckDB timestamps are serialised as ISO-8601 strings."""
        from datetime import date, datetime

        row = SegmentRow.model_validate(
            {
                "dms_segment_id": 1,
                "cookie_reach_updated_at": datetime(2026, 8, 26, 12, 0, 0),
                "ios_reach_updated_at": date(2026, 8, 26),
            }
        )
        assert row.cookie_reach_updated_at == "2026-08-26T12:00:00"
        assert row.ios_reach_updated_at == "2026-08-26"


class TestSegmentsPage:
    """``SegmentsPage`` shape."""

    def test_valid(self) -> None:
        """A page with one dump row round-trips."""
        page = SegmentsPage(
            pagination=PageInfo.from_total(1, 50, 1),
            items=[SegmentRow(dms_segment_id=1001, segment_name="Segment 1001")],
        )
        assert page.items[0].dms_segment_id == 1001
        assert page.pagination.total_items == 1


class TestErrorResponse:
    """Error envelope."""

    def test_valid(self) -> None:
        """Error code and detail are required strings."""
        body = ErrorResponse(error="TAG_NOT_FOUND", detail="Unknown tag 'x'")
        assert body.model_dump() == {"error": "TAG_NOT_FOUND", "detail": "Unknown tag 'x'"}


class TestHealthStatus:
    """Liveness payload."""

    def test_default_ok(self) -> None:
        """Default status is ``ok``."""
        assert HealthStatus().model_dump() == {"status": "ok"}

    def test_extra_fields_forbidden(self) -> None:
        """Unknown fields are rejected."""
        with pytest.raises(ValidationError):
            HealthStatus.model_validate({"status": "ok", "extra": True})
