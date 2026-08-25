"""Unit tests for the CSV catalog repository.

All tests write small fixture files under ``tmp_path`` — no network, no
BigQuery, no LLM calls.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Final

import pytest

from lr_bestsellers.config import DEFAULT_CSV_CATALOG_PATH
from lr_bestsellers.exceptions import CatalogError
from lr_bestsellers.models.catalog import PageRequest, SegmentFeatureRow
from lr_bestsellers.store.csv_catalog import CsvCatalogRepository

CSV_COLUMNS: Final[tuple[str, ...]] = (
    "dms_segment_id",
    "segment_name",
    "segment_description",
    "segment_type",
    "seller_customer_id",
    "active_platform_names",
    "usage_platform_names",
    "active_destination_accounts",
    "active_buyers",
    "active_distribution_platforms",
    "buyers_with_usage",
    "platforms_with_usage",
    "impressions",
    "gross_data_revenue",
    "provider_net_revenue",
    "liveramp_net_revenue",
    "distribution_rank",
    "impressions_rank",
    "provider_revenue_rank",
    "buyer_usage_rank",
    "platform_usage_rank",
    "popularity_score",
    "popularity_rank",
    "is_highly_distributed",
    "is_highly_used",
    "is_top_n_popular",
    "usage_start_date",
    "usage_end_date",
)
"""Column order of the BigQuery dump."""


def make_row(index: int, **overrides: str) -> dict[str, str]:
    """Build one CSV row as raw strings, mirroring a BigQuery export.

    Args:
        index: Row number used to vary ids and ranks.
        **overrides: Column values to replace.

    Returns:
        Mapping of every column in ``CSV_COLUMNS`` to a string value.
    """
    row = {
        "dms_segment_id": str(1000 + index),
        "segment_name": f"Provider > Category > Segment {index}",
        "segment_description": f"Description {index}",
        "segment_type": "Syndicated",
        "seller_customer_id": "506526",
        "active_platform_names": "Beeswax, The Trade Desk, Xandr",
        "usage_platform_names": "The Trade Desk",
        "active_destination_accounts": "12",
        "active_buyers": "5",
        "active_distribution_platforms": "4",
        "buyers_with_usage": "2",
        "platforms_with_usage": "1",
        "impressions": "125000.5",
        "gross_data_revenue": "980.25",
        "provider_net_revenue": "-12.5",
        "liveramp_net_revenue": "300.75",
        "distribution_rank": str(index + 1),
        "impressions_rank": str(index + 1),
        "provider_revenue_rank": str(index + 1),
        "buyer_usage_rank": str(index + 1),
        "platform_usage_rank": str(index + 1),
        "popularity_score": "0.0123",
        "popularity_rank": str(index + 1),
        "is_highly_distributed": "true",
        "is_highly_used": "false",
        "is_top_n_popular": "true",
        "usage_start_date": "2026-07-26",
        "usage_end_date": "2026-08-24",
    }
    row.update(overrides)
    return row


def write_csv(path: Path, rows: list[dict[str, str]]) -> Path:
    """Write ``rows`` to ``path`` using the dump's column order.

    Args:
        path: Destination file.
        rows: Rows to write.

    Returns:
        The path that was written.
    """
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(CSV_COLUMNS))
        writer.writeheader()
        writer.writerows(rows)
    return path


@pytest.fixture
def catalog_csv(tmp_path: Path) -> Path:
    """Write a 5-row catalog dump.

    Args:
        tmp_path: pytest temporary directory.

    Returns:
        Path to the fixture CSV.
    """
    return write_csv(tmp_path / "segments.csv", [make_row(i) for i in range(5)])


class TestSegmentFeatureRow:
    """Tests for row-level parsing of raw CSV strings."""

    def test_parses_types(self) -> None:
        """Strings from the dump are coerced to ints, floats, bools, and dates."""
        row = SegmentFeatureRow.model_validate(make_row(0))
        assert row.dms_segment_id == 1000
        assert row.impressions == pytest.approx(125000.5)
        assert row.provider_net_revenue == pytest.approx(-12.5)
        assert row.is_highly_distributed is True
        assert row.is_highly_used is False
        assert row.usage_end_date.isoformat() == "2026-08-24"

    def test_splits_platform_names(self) -> None:
        """Comma-joined platform aggregates become lists."""
        row = SegmentFeatureRow.model_validate(make_row(0))
        assert row.active_platform_names == ["Beeswax", "The Trade Desk", "Xandr"]
        assert row.usage_platform_names == ["The Trade Desk"]

    def test_empty_platform_names(self) -> None:
        """An empty aggregate column becomes an empty list."""
        row = SegmentFeatureRow.model_validate(make_row(0, usage_platform_names=""))
        assert row.usage_platform_names == []

    def test_blank_description_becomes_null(self) -> None:
        """Blank descriptions are normalised to None."""
        row = SegmentFeatureRow.model_validate(make_row(0, segment_description="   "))
        assert row.segment_description is None


class TestCsvCatalogRepository:
    """Tests for pagination and error handling."""

    def test_row_count(self, catalog_csv: Path) -> None:
        """row_count() reports the number of data rows."""
        assert CsvCatalogRepository(catalog_csv).row_count() == 5

    def test_first_page(self, catalog_csv: Path) -> None:
        """The first page returns rows in dump order with correct metadata."""
        page = CsvCatalogRepository(catalog_csv).page(PageRequest(page=1, page_size=2))
        assert page.mode == "catalog"
        assert page.source == "segments.csv"
        assert [row.dms_segment_id for row in page.items] == [1000, 1001]
        assert page.pagination.total_items == 5
        assert page.pagination.total_pages == 3
        assert page.pagination.has_next is True
        assert page.pagination.has_previous is False

    def test_middle_page(self, catalog_csv: Path) -> None:
        """A middle page has both neighbours."""
        page = CsvCatalogRepository(catalog_csv).page(PageRequest(page=2, page_size=2))
        assert [row.dms_segment_id for row in page.items] == [1002, 1003]
        assert page.pagination.has_next is True
        assert page.pagination.has_previous is True

    def test_last_partial_page(self, catalog_csv: Path) -> None:
        """The final page may be shorter than page_size and has no next page."""
        page = CsvCatalogRepository(catalog_csv).page(PageRequest(page=3, page_size=2))
        assert [row.dms_segment_id for row in page.items] == [1004]
        assert page.pagination.has_next is False
        assert page.pagination.has_previous is True

    def test_page_past_end_is_empty(self, catalog_csv: Path) -> None:
        """Requesting a window past the last row yields no items, not an error."""
        page = CsvCatalogRepository(catalog_csv).page(PageRequest(page=99, page_size=2))
        assert page.items == []
        assert page.pagination.has_next is False

    def test_file_parsed_once(self, catalog_csv: Path) -> None:
        """The dump is parsed on first access and cached afterwards."""
        repo = CsvCatalogRepository(catalog_csv)
        first = repo.page(PageRequest(page=1, page_size=1))
        catalog_csv.unlink()
        second = repo.page(PageRequest(page=1, page_size=1))
        assert first.items[0].dms_segment_id == second.items[0].dms_segment_id

    def test_missing_file(self, tmp_path: Path) -> None:
        """A missing dump raises CatalogError on first access, not at construction."""
        repo = CsvCatalogRepository(tmp_path / "absent.csv")
        with pytest.raises(CatalogError):
            repo.page(PageRequest())

    def test_empty_file(self, tmp_path: Path) -> None:
        """A file without a header row raises CatalogError."""
        path = tmp_path / "empty.csv"
        path.write_text("", encoding="utf-8")
        with pytest.raises(CatalogError):
            CsvCatalogRepository(path).row_count()

    def test_invalid_row(self, tmp_path: Path) -> None:
        """A row with a non-numeric metric raises CatalogError."""
        path = write_csv(
            tmp_path / "bad.csv",
            [make_row(0), make_row(1, impressions="not-a-number")],
        )
        with pytest.raises(CatalogError):
            CsvCatalogRepository(path).row_count()

    def test_missing_column(self, tmp_path: Path) -> None:
        """A dump missing a required column raises CatalogError."""
        path = tmp_path / "short.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            handle.write("dms_segment_id,segment_name\n1,Some Segment\n")
        with pytest.raises(CatalogError):
            CsvCatalogRepository(path).row_count()


@pytest.mark.skipif(
    not DEFAULT_CSV_CATALOG_PATH.exists(),
    reason="BigQuery CSV dump not present in this checkout",
)
def test_real_dump_header_matches_model() -> None:
    """The committed dump's header matches the fields of SegmentFeatureRow."""
    with DEFAULT_CSV_CATALOG_PATH.open(newline="", encoding="utf-8") as handle:
        header = next(csv.reader(handle))
    assert tuple(header) == CSV_COLUMNS
    assert set(header) == set(SegmentFeatureRow.model_fields)
