"""Unit tests for file and glossary ingestion (no I/O beyond tmp fixtures)."""

from __future__ import annotations

from pathlib import Path

import pytest

from lr_bestsellers.exceptions import IngestionError
from lr_bestsellers.ingestion.file_ingestion import FileIngestionSource
from lr_bestsellers.ingestion.glossary_builder import GlossaryIngestionSource
from lr_bestsellers.ingestion.protocols import RawDocument, documents_to_records, embed_and_upsert
from lr_bestsellers.store.protocols import (
    COLLECTION_DOMAIN_KNOWLEDGE,
    COLLECTION_GLOSSARY,
    UpsertRecord,
)
from lr_bestsellers.utils.embeddings import HashEmbedder

_MD = """## Overview

Cookie reach measures the LiveRamp cookie graph for a syndicated segment.

## Platforms

TTD and DV360 are common Connect destinations.
"""

_GLOSSARY = """# Glossary

## cookie_reach

Estimated cookie-graph size.

## SSA

Syndicated segment activation workflow.
"""


class FakeStore:
    """Minimal upsert collector for ingest tests."""

    def __init__(self) -> None:
        """Create an empty collection map."""
        self.written: dict[str, list[UpsertRecord]] = {}

    def upsert(self, collection: str, records: list[UpsertRecord]) -> int:
        """Store records by collection.

        Args:
            collection: Collection name.
            records: Points.

        Returns:
            Count upserted.
        """
        self.written.setdefault(collection, []).extend(records)
        return len(records)


class TestFileIngestionSource:
    """Tests for markdown file ingestion."""

    def test_chunks_fixture_markdown(self, tmp_path: Path) -> None:
        """File source yields prefixed children and skips glossary.md."""
        (tmp_path / "activation.md").write_text(_MD, encoding="utf-8")
        (tmp_path / "glossary.md").write_text(_GLOSSARY, encoding="utf-8")
        source = FileIngestionSource(tmp_path)
        docs = source.load()
        assert source.collection == COLLECTION_DOMAIN_KNOWLEDGE
        assert docs
        assert all(d.filename == "activation.md" for d in docs)
        assert all(d.text.startswith("[Doc: activation.md |") for d in docs)
        assert all(d.collection == COLLECTION_DOMAIN_KNOWLEDGE for d in docs)

    def test_only_file_filter(self, tmp_path: Path) -> None:
        """only_file limits ingestion to one markdown file."""
        (tmp_path / "activation.md").write_text(_MD, encoding="utf-8")
        (tmp_path / "platforms.md").write_text("## Other\n\nNope.", encoding="utf-8")
        source = FileIngestionSource(tmp_path, only_file="knowledge_base/activation.md")
        docs = source.load()
        assert docs
        assert all(d.filename == "activation.md" for d in docs)

    def test_missing_dir_raises(self, tmp_path: Path) -> None:
        """Missing knowledge_base directory raises IngestionError."""
        source = FileIngestionSource(tmp_path / "missing")
        with pytest.raises(IngestionError):
            source.load()


class TestGlossaryIngestionSource:
    """Tests for glossary parsing."""

    def test_one_document_per_term(self, tmp_path: Path) -> None:
        """Each H2 term becomes one glossary document."""
        path = tmp_path / "glossary.md"
        path.write_text(_GLOSSARY, encoding="utf-8")
        source = GlossaryIngestionSource(path)
        docs = source.load()
        assert source.collection == COLLECTION_GLOSSARY
        terms = {d.section for d in docs}
        assert "cookie_reach" in terms
        assert "SSA" in terms
        assert all(d.point_id.startswith("glossary_") for d in docs)

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        """Missing glossary file raises IngestionError."""
        source = GlossaryIngestionSource(tmp_path / "nope.md")
        with pytest.raises(IngestionError):
            source.load()


class TestEmbedAndUpsert:
    """Tests for the shared load → embed → upsert helper."""

    def test_writes_vectors(self, tmp_path: Path) -> None:
        """embed_and_upsert stores 768-dim vectors on the fake store."""
        (tmp_path / "activation.md").write_text(_MD, encoding="utf-8")
        source = FileIngestionSource(tmp_path)
        store = FakeStore()
        count = embed_and_upsert(source, HashEmbedder(), store)
        assert count > 0
        records = store.written[COLLECTION_DOMAIN_KNOWLEDGE]
        assert len(records) == count
        assert len(records[0].dense_vector) == 768

    def test_iter_pages_embeds_each_page(self) -> None:
        """Sources with iter_pages embed and upsert one page at a time."""
        page_one = RawDocument(
            point_id="1",
            text="Segment one",
            collection=COLLECTION_DOMAIN_KNOWLEDGE,
            section="one",
        )
        page_two = RawDocument(
            point_id="2",
            text="Segment two",
            collection=COLLECTION_DOMAIN_KNOWLEDGE,
            section="two",
        )

        class PagedSource:
            """Minimal paged ingestion source."""

            @property
            def name(self) -> str:
                """Return source name."""
                return "bq"

            @property
            def collection(self) -> str:
                """Return collection name."""
                return COLLECTION_DOMAIN_KNOWLEDGE

            def load(self) -> list[RawDocument]:
                """Unused when iter_pages exists."""
                raise AssertionError("load should not run when iter_pages exists")

            def iter_pages(self) -> list[list[RawDocument]]:
                """Yield two pages."""
                return [[page_one], [page_two]]

        store = FakeStore()
        count = embed_and_upsert(PagedSource(), HashEmbedder(), store)
        assert count == 2
        assert len(store.written[COLLECTION_DOMAIN_KNOWLEDGE]) == 2

    def test_documents_to_records_length_mismatch(self) -> None:
        """Mismatched vector list raises ValueError."""
        with pytest.raises(ValueError):
            documents_to_records([], [[0.0] * 768])
