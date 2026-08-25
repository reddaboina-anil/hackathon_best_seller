"""BigQuery ingestion of the bestsellers catalog into ``segment_catalog``.

``settings.bigquery_project`` / ``bq_project`` is the **billing** project.
Catalog ingest reads ``segment_catalog.sql`` (names / descriptions only).
Live metrics stay in ``best_sellers.sql`` and run at query time.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Final

import structlog
from google.cloud import bigquery
from google.oauth2 import service_account

from lr_bestsellers.config import Settings
from lr_bestsellers.exceptions import IngestionError
from lr_bestsellers.ingestion.protocols import RawDocument
from lr_bestsellers.models.segment import SegmentDocument
from lr_bestsellers.store.protocols import COLLECTION_SEGMENT_CATALOG
from lr_bestsellers.utils.chunking import count_tokens

log = structlog.get_logger(__name__)

# jobs.insert (used by client.query) is not allowed on bigquery.readonly.
_BQ_SCOPE = "https://www.googleapis.com/auth/bigquery"
CATALOG_PAGE_SIZE: Final[int] = 100
"""Rows fetched, embedded, and upserted per BigQuery page."""


def build_bigquery_client(settings: Settings) -> bigquery.Client:
    """Construct a BigQuery client using an explicit SA file or ADC.

    Args:
        settings: Application settings. ``bigquery_project`` is the billing
            project. ``google_application_credentials`` is an optional path to
            a service-account JSON key.

    Returns:
        Authenticated ``bigquery.Client``.

    Raises:
        IngestionError: When the credentials file cannot be loaded.
    """
    project = settings.bq_project
    if settings.google_application_credentials:
        try:
            credentials = service_account.Credentials.from_service_account_file(  # type: ignore[no-untyped-call]
                settings.google_application_credentials,
                scopes=[_BQ_SCOPE],
            )
        except (OSError, ValueError) as exc:
            log.error(
                "bq.credentials_failed",
                path=settings.google_application_credentials,
                error=str(exc),
            )
            raise IngestionError("Failed to load Google application credentials") from exc
        log.info("bq.client_sa", billing_project=project)
        return bigquery.Client(project=project, credentials=credentials)
    log.info("bq.client_adc", billing_project=project)
    return bigquery.Client(project=project)


class BigQueryIngestionSource:
    """Run ``segment_catalog.sql`` and emit segment catalog documents.

    Metrics columns are not stored in Qdrant — only id, seller, name, and
    description (via :meth:`SegmentDocument.to_embedding_text`).

    Args:
        settings: App settings (billing project + optional SA path).
        sql_path: Path to ``segment_catalog.sql``.
        client: Optional injected BigQuery client (tests).
    """

    def __init__(
        self,
        settings: Settings,
        sql_path: Path,
        client: bigquery.Client | None = None,
    ) -> None:
        """Store settings, SQL path, and optional client.

        Args:
            settings: Application settings.
            sql_path: Catalog SQL file.
            client: Injected client; constructed lazily in :meth:`load` if None.
        """
        self._settings = settings
        self._sql_path = sql_path
        self._client = client

    @property
    def name(self) -> str:
        """Return the source name ``bq``."""
        return "bq"

    @property
    def collection(self) -> str:
        """Return ``segment_catalog``."""
        return str(COLLECTION_SEGMENT_CATALOG)

    def load(self) -> list[RawDocument]:
        """Load the full catalog by concatenating paged BigQuery results.

        Returns:
            One document per syndicated segment row.

        Raises:
            IngestionError: When the SQL file or BigQuery job fails.
        """
        documents: list[RawDocument] = []
        for page in self.iter_pages():
            documents.extend(page)
        log.info("bq.catalog_loaded", segments=len(documents))
        return documents

    def iter_pages(self) -> Iterator[list[RawDocument]]:
        """Yield catalog documents in ``CATALOG_PAGE_SIZE`` chunks.

        Each page is a separate ``LIMIT`` / ``OFFSET`` query so BigQuery and
        the embedding API stay bounded. ``best_sellers.sql`` is unchanged.

        Yields:
            One page of raw documents (may be shorter on the last page).

        Raises:
            IngestionError: When the SQL file or a page query fails.
        """
        try:
            sql_body = self._sql_path.read_text(encoding="utf-8").strip().rstrip(";")
        except OSError as exc:
            log.error("bq.sql_read_failed", path=str(self._sql_path), error=str(exc))
            raise IngestionError(f"Failed to read SQL file {self._sql_path}") from exc

        paged_sql = (
            f"{sql_body}\nORDER BY dms_segment_id\nLIMIT @limit OFFSET @offset"
        )
        client = self._client or build_bigquery_client(self._settings)
        offset = 0
        page_number = 0
        while True:
            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("limit", "INT64", CATALOG_PAGE_SIZE),
                    bigquery.ScalarQueryParameter("offset", "INT64", offset),
                ]
            )
            try:
                job = client.query(paged_sql, location="US", job_config=job_config)
                log.info(
                    "bq.page_submitted",
                    page=page_number,
                    offset=offset,
                    limit=CATALOG_PAGE_SIZE,
                    job_id=job.job_id,
                    billing_project=self._settings.bq_project,
                )
                result = job.result(timeout=180)
                rows: list[Mapping[str, object]] = [dict(row) for row in result]
            except Exception as exc:
                log.error(
                    "bq.query_failed",
                    error=str(exc),
                    page=page_number,
                    billing_project=self._settings.bq_project,
                )
                raise IngestionError("BigQuery catalog page query failed") from exc
            log.info(
                "bq.page_done",
                page=page_number,
                rows=len(rows),
                bytes_processed=job.total_bytes_processed,
            )
            if not rows:
                break
            yield [_document_from_row(row, self.collection) for row in rows]
            if len(rows) < CATALOG_PAGE_SIZE:
                break
            offset += CATALOG_PAGE_SIZE
            page_number += 1


def _document_from_row(row: Mapping[str, object], collection: str) -> RawDocument:
    """Map a catalog SQL row to a ``RawDocument``.

    Args:
        row: BigQuery row.
        collection: Target Qdrant collection name.

    Returns:
        Document ready to embed.
    """
    doc = _row_to_segment(row)
    text = doc.to_embedding_text()
    return RawDocument(
        point_id=doc.dms_segment_id,
        text=text,
        collection=collection,
        parent_text=text,
        filename="segment_catalog.sql",
        section=doc.name,
        parent_id=doc.dms_segment_id,
        token_count=count_tokens(text),
        dms_segment_id=doc.dms_segment_id,
        seller_customer_id=doc.seller_customer_id,
    )


def _row_to_segment(row: Mapping[str, object]) -> SegmentDocument:
    """Map a BigQuery row to ``SegmentDocument``.

    Args:
        row: Mapping-like query row.

    Returns:
        Catalog document (metrics discarded).
    """
    dms_id = str(row["dms_segment_id"])
    seller = str(row["seller_customer_id"] or "")
    name = str(row["segment_name"] or "")
    description = str(row["segment_description"] or "")
    return SegmentDocument(
        dms_segment_id=dms_id,
        seller_customer_id=seller or "unknown",
        name=name or dms_id,
        description=description or name or dms_id,
    )
