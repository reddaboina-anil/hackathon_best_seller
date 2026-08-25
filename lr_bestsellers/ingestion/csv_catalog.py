"""Local CSV ingest into ``segment_catalog`` (BigQuery export).

Live metrics still run from ``best_sellers.sql`` at query time. Use
``--source csv`` so a full ``refresh`` does not accidentally embed every
row in a large export.

Example::

    uv run python -m lr_bestsellers refresh --source csv
    uv run python -m lr_bestsellers refresh --source csv --file /path/to/export.csv
"""

from __future__ import annotations

import csv
from collections.abc import Iterator
from pathlib import Path
from typing import Final

import structlog

from lr_bestsellers.exceptions import IngestionError
from lr_bestsellers.ingestion.catalog_docs import document_from_catalog_row
from lr_bestsellers.ingestion.protocols import RawDocument
from lr_bestsellers.store.protocols import COLLECTION_SEGMENT_CATALOG
from lr_bestsellers.utils.embeddings import EMBED_BATCH_SIZE

log = structlog.get_logger(__name__)

DEFAULT_CATALOG_CSV: Final[str] = "dms_segments_best_sellers.csv"
"""Default BigQuery export filename placed in the repo root (cwd)."""

_REQUIRED_COLUMNS: Final[frozenset[str]] = frozenset(
    {
        "dms_segment_id",
        "seller_customer_id",
        "segment_name",
        "segment_description",
    }
)


class CsvCatalogIngestionSource:
    """Read a BigQuery CSV export and emit segment catalog documents.

    Pages are yielded in chunks of :data:`~lr_bestsellers.utils.embeddings.EMBED_BATCH_SIZE`
    (default 100) so embedding and upsert happen incrementally without loading
    the entire CSV into memory first.

    Args:
        csv_path: Path to the exported CSV file.

    Example:
        >>> from pathlib import Path
        >>> source = CsvCatalogIngestionSource(Path("dms_segmets_best_sellers.csv"))
        >>> source.name
        'csv'
    """

    def __init__(self, csv_path: Path) -> None:
        """Store the CSV path.

        Args:
            csv_path: Local catalog export (BigQuery UI download or ``bq extract``).
        """
        self._csv_path = csv_path

    @property
    def name(self) -> str:
        """Return the source name ``csv``."""
        return "csv"

    @property
    def collection(self) -> str:
        """Return ``segment_catalog``."""
        return str(COLLECTION_SEGMENT_CATALOG)

    def load(self) -> list[RawDocument]:
        """Load every row from the CSV into memory.

        Returns:
            One document per data row.

        Raises:
            IngestionError: When the file is missing or headers are wrong.
        """
        documents: list[RawDocument] = []
        for page in self.iter_pages():
            documents.extend(page)
        log.info(
            "csv.catalog_loaded",
            path=str(self._csv_path),
            segments=len(documents),
        )
        return documents

    def iter_pages(self) -> Iterator[list[RawDocument]]:
        """Yield catalog documents in :data:`~lr_bestsellers.utils.embeddings.EMBED_BATCH_SIZE` chunks.

        This is the preferred path: ``embed_and_upsert`` calls this so each
        page is embedded and upserted before the next page is read, keeping
        memory use bounded.

        Yields:
            One page of raw documents (shorter on the last page).

        Raises:
            IngestionError: When the file cannot be opened or required headers
                are missing.
        """
        try:
            handle = self._csv_path.open(encoding="utf-8-sig", newline="")
        except OSError as exc:
            log.error("csv.open_failed", path=str(self._csv_path), error=str(exc))
            raise IngestionError(f"Cannot read catalog CSV {self._csv_path}") from exc

        with handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                raise IngestionError(f"Catalog CSV {self._csv_path} has no header row")
            columns = {_normalise(c) for c in reader.fieldnames if c}
            missing = _REQUIRED_COLUMNS - columns
            if missing:
                raise IngestionError(
                    f"Catalog CSV {self._csv_path} missing columns: "
                    f"{sorted(missing)!r}. "
                    f"Found: {sorted(columns)!r}"
                )

            page: list[RawDocument] = []
            page_number = 0
            skipped = 0

            for raw in reader:
                row = {_normalise(k): v for k, v in raw.items() if k}
                if not str(row.get("dms_segment_id") or "").strip():
                    skipped += 1
                    continue
                page.append(
                    document_from_catalog_row(
                        row,
                        self.collection,
                        filename=self._csv_path.name,
                    )
                )
                if len(page) >= EMBED_BATCH_SIZE:
                    log.info(
                        "csv.page_ready",
                        page=page_number,
                        rows=len(page),
                    )
                    yield page
                    page_number += 1
                    page = []

            if page:
                log.info("csv.page_ready", page=page_number, rows=len(page))
                yield page

            if skipped:
                log.warning(
                    "csv.rows_skipped",
                    skipped=skipped,
                    path=str(self._csv_path),
                )


def _normalise(name: str) -> str:
    """Lowercase and strip a CSV header for case-insensitive matching.

    Args:
        name: Raw header cell value.

    Returns:
        Normalised column name.
    """
    return name.strip().lower()
