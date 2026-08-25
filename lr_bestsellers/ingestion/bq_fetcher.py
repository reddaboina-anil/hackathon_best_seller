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
from lr_bestsellers.ingestion.catalog_docs import document_from_catalog_row
from lr_bestsellers.ingestion.protocols import RawDocument
from lr_bestsellers.store.protocols import COLLECTION_PLATFORM_NAMES, COLLECTION_SEGMENT_CATALOG

log = structlog.get_logger(__name__)

# jobs.insert (used by client.query) is not allowed on bigquery.readonly.
_BQ_SCOPE = "https://www.googleapis.com/auth/bigquery"
CATALOG_PAGE_SIZE: Final[int] = 1000
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
            yield [
                document_from_catalog_row(
                    row,
                    self.collection,
                    filename="segment_catalog.sql",
                )
                for row in rows
            ]
            if len(rows) < CATALOG_PAGE_SIZE:
                break
            offset += CATALOG_PAGE_SIZE
            page_number += 1


_PLATFORM_NAMES_SQL: str = (
    "SELECT DISTINCT TRIM(platform) AS platform_name\n"
    "FROM UNNEST(SPLIT(active_platform_names, ', ')) AS platform\n"
    "WHERE TRIM(platform) != ''"
)


class PlatformNamesSource:
    """Ingest distinct canonical platform names from BigQuery into Qdrant.

    At every ``refresh --source platform_names`` run, this source queries
    the live pipeline SQL to collect all distinct values from the
    ``active_platform_names`` column and upserts them into the
    ``platform_names`` Qdrant collection for sparse BM25 lookup.

    Args:
        settings: App settings (billing project + optional SA path).
        pipeline_sql: Body of ``best_sellers.sql`` (no trailing semicolon).
        client: Optional injected BigQuery client (tests).
    """

    def __init__(
        self,
        settings: Settings,
        pipeline_sql: str,
        client: object | None = None,
    ) -> None:
        """Store settings, pipeline SQL, and optional client.

        Args:
            settings: Application settings.
            pipeline_sql: Best-sellers pipeline SQL used as a CTE.
            client: Optional injected BigQuery client.
        """
        self._settings = settings
        self._pipeline_sql = pipeline_sql.strip().rstrip(";")
        self._client = client

    @property
    def name(self) -> str:
        """Return the source name ``platform_names``."""
        return "platform_names"

    @property
    def collection(self) -> str:
        """Return ``platform_names``."""
        return str(COLLECTION_PLATFORM_NAMES)

    def load(self) -> list[RawDocument]:
        """Query BigQuery for distinct platform names.

        Wraps the full pipeline SQL in a sub-CTE to extract distinct values
        from ``active_platform_names``.

        Returns:
            One ``RawDocument`` per distinct canonical platform name.

        Raises:
            IngestionError: When the BigQuery job fails.
        """
        if not self._pipeline_sql:
            log.warning("platform_names.empty_pipeline", note="No pipeline SQL; skipping")
            return []

        pipeline = self._pipeline_sql
        wrapped_sql = (
            f"WITH bestsellers_cte AS (\n{pipeline}\n)\n"
            "SELECT DISTINCT TRIM(platform) AS platform_name\n"
            "FROM UNNEST(SPLIT(\n"
            "  (SELECT STRING_AGG(active_platform_names, ', ') FROM bestsellers_cte),\n"
            "  ', '\n"
            ")) AS platform\n"
            "WHERE TRIM(platform) != ''"
        )

        client = self._client or build_bigquery_client(self._settings)
        try:
            query_fn = getattr(client, "query")
            job = query_fn(wrapped_sql, location="US")
            result = job.result(timeout=180)
            rows: list[object] = [dict(row) for row in result]  # type: ignore[arg-type]
        except Exception as exc:
            log.error(
                "platform_names.query_failed",
                error=str(exc),
                billing_project=self._settings.bq_project,
            )
            raise IngestionError("PlatformNamesSource BigQuery query failed") from exc

        documents: list[RawDocument] = []
        for i, row in enumerate(rows):
            row_dict = row if isinstance(row, dict) else {}
            name_val = str(row_dict.get("platform_name", "")).strip()
            if not name_val:
                continue
            documents.append(
                RawDocument(
                    point_id=f"platform_name_{i}",
                    text=name_val,
                    collection=self.collection,
                    section="platform_names",
                    filename="bigquery:active_platform_names",
                    token_count=max(1, len(name_val.split())),
                )
            )
        log.info("platform_names.loaded", count=len(documents))
        return documents

