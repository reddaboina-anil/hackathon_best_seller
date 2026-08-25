"""Ingestion package: file, BigQuery, and glossary sources."""

from __future__ import annotations

from lr_bestsellers.ingestion.bq_fetcher import BigQueryIngestionSource, build_bigquery_client
from lr_bestsellers.ingestion.file_ingestion import FileIngestionSource
from lr_bestsellers.ingestion.glossary_builder import GlossaryIngestionSource
from lr_bestsellers.ingestion.protocols import (
    IngestionSourceProtocol,
    RawDocument,
    embed_and_upsert,
)

__all__ = [
    "BigQueryIngestionSource",
    "FileIngestionSource",
    "GlossaryIngestionSource",
    "IngestionSourceProtocol",
    "RawDocument",
    "build_bigquery_client",
    "embed_and_upsert",
]
