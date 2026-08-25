"""BigQuery connectivity integration test (skipped without credentials)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from lr_bestsellers.config import Settings
from lr_bestsellers.ingestion.bq_fetcher import BigQueryIngestionSource, build_bigquery_client


def _can_reach_bq() -> bool:
    """Return True when settings and ADC/SA allow a BigQuery client.

    Returns:
        ``True`` if constructing a client does not raise.
    """
    if not os.environ.get("GOOGLE_API_KEY") and not os.environ.get("BIGQUERY_PROJECT"):
        return False
    try:
        settings = Settings(
            google_api_key=os.environ.get("GOOGLE_API_KEY", "test-key"),
            bigquery_project=os.environ.get("BIGQUERY_PROJECT", "liveramp-eng-qa-reliability"),
            google_application_credentials=os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"),
        )
        build_bigquery_client(settings)
    except Exception:
        return False
    return True


@pytest.mark.skipif(not _can_reach_bq(), reason="BigQuery credentials are not configured")
def test_bestsellers_sql_returns_rows() -> None:
    """Running segment_catalog.sql returns at least one catalog row."""
    settings = Settings(
        google_api_key=os.environ.get("GOOGLE_API_KEY", "test-key"),
        bigquery_project=os.environ.get("BIGQUERY_PROJECT", "liveramp-eng-qa-reliability"),
        google_application_credentials=os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"),
    )
    root = Path(__file__).resolve().parents[2]
    source = BigQueryIngestionSource(settings, root / "segment_catalog.sql")
    docs = source.load()
    assert docs
    assert docs[0].dms_segment_id
    assert "Segment:" in docs[0].text
