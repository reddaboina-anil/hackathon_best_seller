"""Read-only repository over the offline BigQuery CSV dump.

The dump (``csv_dump/segment_recommendation_features.csv``) is a snapshot of the
segment recommendation features table. It is small enough (tens of thousands of
rows) to parse once and keep in memory, so the first request pays the parse cost
and every later request is a slice of a tuple.

Rows are returned in dump order, which BigQuery already sorted by popularity —
this keeps pagination stable across requests.
"""

from __future__ import annotations

import csv
import math
import threading
from pathlib import Path
from typing import Final

import structlog
from pydantic import ValidationError

from lr_bestsellers.exceptions import CatalogError
from lr_bestsellers.models.catalog import (
    CatalogPage,
    PageInfo,
    PageRequest,
    SegmentFeatureRow,
)

log = structlog.get_logger(__name__)

_HEADER_LINES: Final[int] = 1
"""Number of header lines skipped by ``csv.DictReader`` when reporting line numbers."""


class CsvCatalogRepository:
    """Serve paginated segment features from a CSV dump of the BigQuery table.

    The file is parsed lazily on first access and cached for the lifetime of the
    instance. Parsing is guarded by a lock because FastAPI runs synchronous
    endpoints in a thread pool.

    Args:
        csv_path: Path to the CSV dump.

    Example:
        >>> repo = CsvCatalogRepository(Path("csv_dump/segment_recommendation_features.csv"))
        >>> page = repo.page(PageRequest(page=1, page_size=10))
        >>> len(page.items)
        10
    """

    def __init__(self, csv_path: Path) -> None:
        """Store the dump location without touching the filesystem.

        Args:
            csv_path: Path to the CSV dump.
        """
        self._csv_path = csv_path
        self._lock = threading.Lock()
        self._rows: tuple[SegmentFeatureRow, ...] | None = None

    @property
    def source_name(self) -> str:
        """Return the dump filename used as the ``source`` label in responses.

        Returns:
            The file name of the configured CSV dump.
        """
        return self._csv_path.name

    def row_count(self) -> int:
        """Return the total number of catalog rows.

        Returns:
            Row count, excluding the header.

        Raises:
            CatalogError: When the dump is missing or malformed.
        """
        return len(self._rows_cached())

    def page(self, request: PageRequest) -> CatalogPage:
        """Return one page of catalog rows plus pagination metadata.

        Args:
            request: Requested pagination window.

        Returns:
            A ``CatalogPage``; ``items`` is empty when the window starts past
            the last row.

        Raises:
            CatalogError: When the dump is missing or malformed.
        """
        rows = self._rows_cached()
        total_items = len(rows)
        window = rows[request.offset : request.offset + request.page_size]
        pagination = PageInfo(
            page=request.page,
            page_size=request.page_size,
            total_items=total_items,
            total_pages=math.ceil(total_items / request.page_size),
            has_next=request.offset + len(window) < total_items,
            has_previous=request.page > 1,
        )
        log.info(
            "catalog.page",
            source=self.source_name,
            page=request.page,
            page_size=request.page_size,
            returned=len(window),
            total_items=total_items,
        )
        return CatalogPage(
            source=self.source_name,
            pagination=pagination,
            items=list(window),
        )

    def _rows_cached(self) -> tuple[SegmentFeatureRow, ...]:
        """Return the parsed rows, loading them on first use.

        Returns:
            All catalog rows in dump order.

        Raises:
            CatalogError: When the dump is missing or malformed.
        """
        with self._lock:
            if self._rows is None:
                self._rows = self._read_rows()
            return self._rows

    def _read_rows(self) -> tuple[SegmentFeatureRow, ...]:
        """Parse and validate every row of the CSV dump.

        Returns:
            All catalog rows in dump order.

        Raises:
            CatalogError: When the file cannot be opened, has no header, or
                contains a row that fails validation.
        """
        try:
            handle = self._csv_path.open(newline="", encoding="utf-8")
        except OSError as exc:
            log.error("catalog.open_failed", path=str(self._csv_path), error=str(exc))
            raise CatalogError(f"Cannot read CSV catalog at {self._csv_path}") from exc

        rows: list[SegmentFeatureRow] = []
        with handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                log.error("catalog.empty_file", path=str(self._csv_path))
                raise CatalogError(f"CSV catalog at {self._csv_path} has no header row")
            for offset, raw_row in enumerate(reader):
                line_number = offset + _HEADER_LINES + 1
                try:
                    rows.append(SegmentFeatureRow.model_validate(raw_row))
                except ValidationError as exc:
                    log.error(
                        "catalog.row_invalid",
                        path=str(self._csv_path),
                        line=line_number,
                        error=str(exc),
                    )
                    raise CatalogError(
                        f"Invalid row at line {line_number} of {self._csv_path}"
                    ) from exc

        log.info("catalog.loaded", path=str(self._csv_path), rows=len(rows))
        return tuple(rows)
