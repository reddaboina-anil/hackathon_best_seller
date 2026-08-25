"""Refresh CLI: re-embed knowledge sources into Qdrant.

Usage::

    uv run python -m lr_bestsellers refresh
    uv run python -m lr_bestsellers refresh --file knowledge_base/activation.md
    uv run python -m lr_bestsellers refresh --source bq --verbose
    uv run python -m lr_bestsellers refresh --reset
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Literal

import structlog
from qdrant_client import QdrantClient

from lr_bestsellers.config import Settings, get_settings
from lr_bestsellers.exceptions import IngestionError
from lr_bestsellers.ingestion.bq_fetcher import BigQueryIngestionSource
from lr_bestsellers.ingestion.csv_catalog import (
    DEFAULT_CATALOG_CSV,
    CsvCatalogIngestionSource,
)
from lr_bestsellers.ingestion.file_ingestion import FileIngestionSource
from lr_bestsellers.ingestion.glossary_builder import GlossaryIngestionSource
from lr_bestsellers.ingestion.protocols import IngestionSourceProtocol, embed_and_upsert
from lr_bestsellers.store.qdrant import QdrantRepository
from lr_bestsellers.utils.embeddings import EmbedderProtocol, GoogleEmbedder, HashEmbedder
from lr_bestsellers.utils.logging import configure_logging

log = structlog.get_logger(__name__)

SourceName = Literal["files", "bq", "glossary", "csv", "all"]


def repo_root() -> Path:
    """Return the process working directory.

    ``uv run`` is invoked from the repository root, so this is
    ``hackathon_best_seller`` (knowledge_base, best_sellers.sql, ``.env``).

    Returns:
        Absolute path to the current working directory.
    """
    return Path.cwd()


def _build_parser() -> argparse.ArgumentParser:
    """Build the top-level CLI parser.

    Returns:
        Configured ``ArgumentParser``.
    """
    parser = argparse.ArgumentParser(prog="lr_bestsellers")
    sub = parser.add_subparsers(dest="command", required=True)
    refresh = sub.add_parser("refresh", help="Re-ingest knowledge sources into Qdrant")
    refresh.add_argument(
        "--file",
        dest="file",
        default=None,
        help=(
            "For --source files: re-ingest a single knowledge_base markdown file. "
            f"For --source csv: path to the CSV export (default: {DEFAULT_CATALOG_CSV})."
        ),
    )
    refresh.add_argument(
        "--source",
        dest="source",
        choices=("files", "bq", "glossary", "csv", "all"),
        default="all",
        help=(
            "Which ingestion source to run. "
            "'all' includes files + glossary + bq (not csv). "
            "'csv' reads a local BigQuery export and is not run by 'all'."
        ),
    )
    refresh.add_argument(
        "--reset",
        action="store_true",
        help="Wipe and recreate Qdrant collections before ingest",
    )
    refresh.add_argument(
        "--verbose",
        action="store_true",
        help="Enable DEBUG logging",
    )
    return parser


def _make_embedder(settings: Settings) -> EmbedderProtocol:
    """Return a Google embedder, falling back to hash embedder if key is placeholder.

    Args:
        settings: Application settings.

    Returns:
        Embedder implementation.
    """
    key = settings.google_api_key.get_secret_value()
    if key.startswith("your-") or key == "fake-api-key":
        log.warning("embedder.hash_fallback", reason="placeholder google_api_key")
        return HashEmbedder()
    return GoogleEmbedder(api_key=key, model=settings.embedding_model)


def _make_store(settings: Settings) -> QdrantRepository:
    """Build a Qdrant repository from settings.

    Args:
        settings: Application settings.

    Returns:
        Connected ``QdrantRepository``.
    """
    api_key = None
    if settings.qdrant_api_key is not None:
        api_key = settings.qdrant_api_key.get_secret_value()
    client = QdrantClient(url=settings.qdrant_url, api_key=api_key)
    return QdrantRepository(client)


def build_sources(
    settings: Settings,
    *,
    source: SourceName,
    only_file: str | None,
    root: Path,
) -> list[IngestionSourceProtocol]:
    """Instantiate the requested ingestion sources.

    ``csv`` is **not** included in ``all``: running a million-row embedding by
    accident would be disruptive. Use ``--source csv`` explicitly.

    Args:
        settings: Application settings.
        source: Source selector.
        only_file: For ``files``: single markdown file. For ``csv``: CSV path
            override (defaults to ``DEFAULT_CATALOG_CSV`` in ``root``).
        root: Repository root (cwd when invoked via ``uv run``).

    Returns:
        Concrete sources to run.
    """
    kb = root / "knowledge_base"
    sql_path = root / "segment_catalog.sql"
    selected: list[IngestionSourceProtocol] = []

    if source == "csv":
        csv_path = (
            Path(only_file) if only_file else root / DEFAULT_CATALOG_CSV
        )
        selected.append(CsvCatalogIngestionSource(csv_path))
        return selected

    want_files = source in ("files", "all") or (
        only_file is not None and source != "bq"
    )
    want_glossary = source in ("glossary", "all") and only_file is None
    want_bq = source in ("bq", "all") and only_file is None
    if source == "files":
        want_glossary = False
        want_bq = False
    if source == "glossary":
        want_files = False
        want_bq = False
    if source == "bq":
        want_files = False
        want_glossary = False
    if want_files:
        selected.append(FileIngestionSource(kb, only_file=only_file))
    if want_glossary:
        selected.append(GlossaryIngestionSource(kb / "glossary.md"))
    if want_bq:
        selected.append(BigQueryIngestionSource(settings, sql_path))
    return selected


def run_refresh(
    settings: Settings,
    *,
    source: SourceName = "all",
    only_file: str | None = None,
    reset: bool = False,
    store: QdrantRepository | None = None,
    embedder: EmbedderProtocol | None = None,
    root: Path | None = None,
) -> int:
    """Run ingestion for the selected sources.

    Args:
        settings: Application settings.
        source: ``files``, ``bq``, ``glossary``, or ``all``.
        only_file: Optional markdown file filter.
        reset: Recreate Qdrant collections first.
        store: Injected store (tests).
        embedder: Injected embedder (tests).
        root: Repository root override (tests).

    Returns:
        Total points upserted.

    Raises:
        IngestionError: When a source fails.
    """
    root_path = root or repo_root()
    repository = store or _make_store(settings)
    if reset:
        repository.recreate_collections()
    else:
        repository.ensure_collections()
    active_embedder = embedder or _make_embedder(settings)
    sources = build_sources(settings, source=source, only_file=only_file, root=root_path)
    total = 0
    for item in sources:
        count = embed_and_upsert(item, active_embedder, repository)
        log.info("refresh.source_done", source=item.name, upserted=count)
        total += count
    log.info("refresh.complete", upserted=total)
    return total


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Argument vector excluding the program name; defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command != "refresh":
        parser.error(f"unknown command {args.command}")
    log_level = "DEBUG" if args.verbose else "INFO"
    configure_logging(log_level)
    try:
        settings = get_settings()
    except Exception as exc:
        log.error("refresh.settings_failed", error=str(exc))
        return 2
    try:
        run_refresh(
            settings,
            source=args.source,
            only_file=args.file,
            reset=args.reset,
        )
    except IngestionError as exc:
        log.error("refresh.failed", error=str(exc))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
