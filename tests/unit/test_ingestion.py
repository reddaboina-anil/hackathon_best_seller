"""Unit tests for file and glossary ingestion (no I/O beyond tmp fixtures)."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from lr_bestsellers.exceptions import IngestionError
from lr_bestsellers.ingestion.csv_catalog import CsvCatalogIngestionSource
from lr_bestsellers.ingestion.file_ingestion import FileIngestionSource
from lr_bestsellers.ingestion.glossary_builder import GlossaryIngestionSource
from lr_bestsellers.ingestion.protocols import RawDocument, documents_to_records, embed_and_upsert
from lr_bestsellers.store.protocols import (
    COLLECTION_DOMAIN_KNOWLEDGE,
    COLLECTION_GLOSSARY,
    COLLECTION_SEGMENT_CATALOG,
    UpsertRecord,
)
from lr_bestsellers.utils.embeddings import EMBED_BATCH_SIZE, HashEmbedder

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


_CSV_HEADER = "dms_segment_id,seller_customer_id,segment_name,segment_description"


class TestCsvCatalogIngestionSource:
    """Tests for CsvCatalogIngestionSource."""

    def _write_csv(self, tmp_path: Path, rows: list[dict[str, str]]) -> Path:
        """Write a minimal CSV to a tmp file and return its path.

        Args:
            tmp_path: pytest tmp directory.
            rows: Row dicts to write.

        Returns:
            Path to the CSV file.
        """
        path = tmp_path / "test_catalog.csv"
        lines = [_CSV_HEADER]
        for r in rows:
            lines.append(
                ",".join(
                    r.get(c, "")
                    for c in (
                        "dms_segment_id",
                        "seller_customer_id",
                        "segment_name",
                        "segment_description",
                    )
                )
            )
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def test_load_returns_one_doc_per_data_row(self, tmp_path: Path) -> None:
        """load() returns one RawDocument for each data row."""
        path = self._write_csv(
            tmp_path,
            [
                {
                    "dms_segment_id": "SEG001",
                    "seller_customer_id": "SELLER1",
                    "segment_name": "Autos",
                    "segment_description": "People interested in autos",
                },
                {
                    "dms_segment_id": "SEG002",
                    "seller_customer_id": "SELLER2",
                    "segment_name": "Finance",
                    "segment_description": "Finance fans",
                },
            ],
        )
        source = CsvCatalogIngestionSource(path)
        docs = source.load()
        assert len(docs) == 2
        ids = {d.dms_segment_id for d in docs}
        assert ids == {"SEG001", "SEG002"}

    def test_source_metadata(self, tmp_path: Path) -> None:
        """name and collection are set correctly."""
        path = self._write_csv(tmp_path, [])
        source = CsvCatalogIngestionSource(path)
        assert source.name == "csv"
        assert source.collection == str(COLLECTION_SEGMENT_CATALOG)

    def test_iter_pages_chunks_by_embed_batch_size(self, tmp_path: Path) -> None:
        """iter_pages yields chunks of at most EMBED_BATCH_SIZE."""
        n = EMBED_BATCH_SIZE + 5
        rows = [
            {
                "dms_segment_id": f"SEG{i:04d}",
                "seller_customer_id": "SELLER",
                "segment_name": f"Segment {i}",
                "segment_description": f"Desc {i}",
            }
            for i in range(n)
        ]
        path = self._write_csv(tmp_path, rows)
        pages = list(CsvCatalogIngestionSource(path).iter_pages())
        assert len(pages) == 2
        assert len(pages[0]) == EMBED_BATCH_SIZE
        assert len(pages[1]) == 5
        all_docs = [d for page in pages for d in page]
        assert len(all_docs) == n

    def test_rows_with_empty_segment_id_are_skipped(self, tmp_path: Path) -> None:
        """Rows missing dms_segment_id are silently skipped."""
        path = tmp_path / "test_catalog.csv"
        path.write_text(
            textwrap.dedent("""\
                dms_segment_id,seller_customer_id,segment_name,segment_description
                ,SELLER,No id,desc
                SEG999,SELLER,Valid,desc
            """),
            encoding="utf-8",
        )
        docs = CsvCatalogIngestionSource(path).load()
        assert len(docs) == 1
        assert docs[0].dms_segment_id == "SEG999"

    def test_missing_file_raises_ingestion_error(self, tmp_path: Path) -> None:
        """Missing CSV file raises IngestionError."""
        source = CsvCatalogIngestionSource(tmp_path / "nonexistent.csv")
        with pytest.raises(IngestionError, match="Cannot read catalog CSV"):
            source.load()

    def test_missing_required_column_raises_ingestion_error(
        self, tmp_path: Path
    ) -> None:
        """CSV without required columns raises IngestionError."""
        path = tmp_path / "bad.csv"
        path.write_text("dms_segment_id,segment_name\nSEG1,Foo\n", encoding="utf-8")
        source = CsvCatalogIngestionSource(path)
        with pytest.raises(IngestionError, match="missing columns"):
            source.load()

    def test_utf8_bom_header_is_accepted(self, tmp_path: Path) -> None:
        """UTF-8 BOM on the first column does not break header detection."""
        path = tmp_path / "bom.csv"
        content = "dms_segment_id,seller_customer_id,segment_name,segment_description\nSEG1,S1,MySegment,A description\n"
        # Write with utf-8-sig so the file has a real BOM byte sequence at the start.
        path.write_text(content, encoding="utf-8-sig")
        docs = CsvCatalogIngestionSource(path).load()
        assert len(docs) == 1
        assert docs[0].dms_segment_id == "SEG1"
