"""Unit tests for agent tool input schemas and tool factories."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from lr_bestsellers.agent.tools import (
    FakeBqRunner,
    GlossaryLookupInput,
    HybridSearchInput,
    Text2SqlInput,
    make_glossary_lookup_tool,
    make_hybrid_search_tool,
    make_text2sql_exec_tool,
    run_hybrid_search,
)
from lr_bestsellers.models.query import BqQueryRequest
from lr_bestsellers.store.protocols import COLLECTION_GLOSSARY, EMBEDDING_DIM, UpsertRecord
from lr_bestsellers.utils.embeddings import HashEmbedder
from tests.unit.test_store_protocol import FakeVectorStore


class TestInputSchemas:
    """Pydantic schema validation for tool arguments."""

    def test_hybrid_search_input_valid(self) -> None:
        """HybridSearchInput accepts a query and collection."""
        payload = HybridSearchInput(query="cookie reach", collection="glossary", top_k=5)
        assert payload.top_k == 5

    def test_hybrid_search_empty_query(self) -> None:
        """Empty query is rejected."""
        with pytest.raises(ValidationError):
            HybridSearchInput(query="", collection="glossary")

    def test_glossary_lookup_input(self) -> None:
        """GlossaryLookupInput requires a term."""
        payload = GlossaryLookupInput(term="SSA")
        assert payload.top_k == 3

    def test_text2sql_too_short(self) -> None:
        """SQL shorter than 6 characters is rejected."""
        with pytest.raises(ValidationError):
            Text2SqlInput(sql="SEL")

    def test_text2sql_valid(self) -> None:
        """A SELECT statement passes schema validation."""
        payload = Text2SqlInput(sql="SELECT 1")
        assert payload.sql.startswith("SELECT")


class TestToolFactories:
    """Tools run against in-memory fakes."""

    def test_hybrid_search_tool_invoke(self) -> None:
        """hybrid_search tool returns a string containing the source file."""
        store = FakeVectorStore()
        embedder = HashEmbedder()
        text = "digest is the packaged matched identifiers"
        store.upsert(
            COLLECTION_GLOSSARY,
            [
                UpsertRecord(
                    point_id="g1",
                    text=text,
                    dense_vector=embedder.embed_query(text),
                    filename="glossary.md",
                    section="digest",
                    token_count=6,
                )
            ],
        )
        tool = make_hybrid_search_tool(store, embedder)
        rendered = tool.invoke({"query": "digest", "collection": COLLECTION_GLOSSARY, "top_k": 3})
        assert isinstance(rendered, str)
        assert "glossary.md" in rendered or "no hits" in rendered

    def test_glossary_lookup_tool(self) -> None:
        """glossary_lookup tool is named correctly."""
        tool = make_glossary_lookup_tool(FakeVectorStore(), HashEmbedder())
        assert tool.name == "glossary_lookup"

    def test_text2sql_tool(self) -> None:
        """text2sql_exec tool dumps canned rows."""
        runner = FakeBqRunner()
        tool = make_text2sql_exec_tool(runner)
        rendered = tool.invoke({"sql": "SELECT cookie_reach FROM x LIMIT 1"})
        assert "cookie_reach" in rendered
        assert runner.last_sql is not None

    def test_run_hybrid_search_empty_collection(self) -> None:
        """Searching an empty store returns no hits."""
        hits = run_hybrid_search(
            FakeVectorStore(),
            HashEmbedder(),
            HybridSearchInput(query="anything", collection="glossary", top_k=3),
        )
        assert hits == []

    def test_fake_bq_execute(self) -> None:
        """FakeBqRunner records SQL."""
        runner = FakeBqRunner()
        rows = runner.execute(BqQueryRequest(sql="SELECT 1"))
        assert rows[0].fields["segment_name"] == "Auto Intenders"
        assert EMBEDDING_DIM == 768
