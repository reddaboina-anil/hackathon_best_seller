"""BigQuery ingestion of the bestsellers catalog into ``segment_catalog``.

``settings.bigquery_project`` / ``bq_project`` is the **billing** project.
Data tables are fully qualified inside ``best_sellers.sql``.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

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

_BQ_READONLY_SCOPE = "https://www.googleapis.com/auth/bigquery.readonly"


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
                scopes=[_BQ_READONLY_SCOPE],
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
    """Run ``best_sellers.sql`` and emit segment catalog documents.

    Metrics columns are not stored in Qdrant — only id, seller, name, and
    description (via :meth:`SegmentDocument.to_embedding_text`).

    Args:
        settings: App settings (billing project + optional SA path).
        sql_path: Path to ``best_sellers.sql``.
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
            sql_path: Bestsellers SQL file.
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
        """Execute the bestsellers query and map rows to raw documents.

        Returns:
            One document per syndicated segment row.

        Raises:
            IngestionError: When the SQL file or BigQuery job fails.
        """
        try:
            sql = self._sql_path.read_text(encoding="utf-8")
        except OSError as exc:
            log.error("bq.sql_read_failed", path=str(self._sql_path), error=str(exc))
            raise IngestionError(f"Failed to read SQL file {self._sql_path}") from exc

        client = self._client or build_bigquery_client(self._settings)
        try:
            job = client.query(sql)
            rows = list(job.result())
        except Exception as exc:
            log.error("bq.query_failed", error=str(exc), billing_project=self._settings.bq_project)
            raise IngestionError("BigQuery bestsellers query failed") from exc

        documents: list[RawDocument] = []
        for row in rows:
            doc = _row_to_segment(row)
            text = doc.to_embedding_text()
            documents.append(
                RawDocument(
                    point_id=doc.dms_segment_id,
                    text=text,
                    collection=self.collection,
                    parent_text=text,
                    filename="best_sellers.sql",
                    section=doc.name,
                    parent_id=doc.dms_segment_id,
                    token_count=count_tokens(text),
                    dms_segment_id=doc.dms_segment_id,
                    seller_customer_id=doc.seller_customer_id,
                )
            )
        log.info("bq.catalog_loaded", segments=len(documents))
        return documents


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
