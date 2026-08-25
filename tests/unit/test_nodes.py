"""Unit tests for LangGraph node functions with mocked collaborators."""

from __future__ import annotations

from lr_bestsellers.agent.graph import empty_state, route_intent
from lr_bestsellers.agent.nodes import (
    FakeLLM,
    NodeContext,
    classify_intent_node,
    keyword_intent,
    parse_intent,
    rerank_results_node,
    run_hybrid_search_node,
    run_text2sql_node,
    synthesize_node,
    threshold_gate_node,
)
from lr_bestsellers.agent.tools import FakeBqRunner
from lr_bestsellers.config import Settings
from lr_bestsellers.models.chunk import ChildChunk, SearchResult
from lr_bestsellers.store.protocols import COLLECTION_DOMAIN_KNOWLEDGE, EMBEDDING_DIM, UpsertRecord
from lr_bestsellers.utils.embeddings import HashEmbedder
from lr_bestsellers.utils.reranker import CrossEncoderReranker
from tests.unit.test_store_protocol import FakeVectorStore


def _settings() -> Settings:
    """Test settings with a low similarity threshold.

    Returns:
        Settings instance.
    """
    return Settings(
        google_api_key="fake-api-key",
        bigquery_project="liveramp-eng-qa-reliability",
        similarity_threshold=0.0,
        top_k_final=3,
    )


def _ctx(
    store: FakeVectorStore | None = None,
    llm: FakeLLM | None = None,
) -> NodeContext:
    """Build a NodeContext with fakes.

    Args:
        store: Optional preloaded store.
        llm: Optional fake LLM.

    Returns:
        Node context.
    """
    return NodeContext(
        settings=_settings(),
        store=store or FakeVectorStore(),
        embedder=HashEmbedder(),
        reranker=CrossEncoderReranker(),
        llm=llm
        or FakeLLM(["conceptual", "Activation is matching plus delivery. [Source: activation.md]"]),
        bq=FakeBqRunner(),
    )


def _hit(score: float) -> SearchResult:
    """Build a SearchResult for threshold tests.

    Args:
        score: Relevance score.

    Returns:
        Search result.
    """
    chunk = ChildChunk(
        chunk_id="c1",
        parent_id="p1",
        text="[Doc: activation.md | Section: Overview] Activation delivers identifiers.",
        filename="activation.md",
        section="Overview",
        token_count=10,
    )
    return SearchResult(
        chunk=chunk,
        score=score,
        collection="domain_knowledge",
        parent_text="Activation delivers identifiers.",
    )


class TestKeywordIntent:
    """Heuristic classifier tests."""

    def test_analytics_top_reach(self) -> None:
        """Top-by-reach questions classify as analytics."""
        assert keyword_intent("What are the top segments by cookie reach?") == "analytics"

    def test_conceptual_what_is(self) -> None:
        """Definition questions classify as conceptual."""
        assert keyword_intent("What is activation?") == "conceptual"

    def test_parse_intent_valid(self) -> None:
        """LLM label is honoured when valid."""
        assert parse_intent("analytics please", "hello") == "analytics"


class TestRouteIntent:
    """Graph routing tests."""

    def test_conceptual_is_vector(self) -> None:
        """Conceptual intent routes to the vector path."""
        state = empty_state("What is SSA?")
        state["intent"] = "conceptual"
        assert route_intent(state) == "vector"

    def test_analytics_is_sql(self) -> None:
        """Analytics intent routes to SQL."""
        state = empty_state("top by reach")
        state["intent"] = "analytics"
        assert route_intent(state) == "sql"

    def test_mixed_is_both(self) -> None:
        """Mixed intent routes to both."""
        state = empty_state("explain reach and list top")
        state["intent"] = "mixed"
        assert route_intent(state) == "both"


class TestNodes:
    """Node function tests."""

    def test_classify_uses_llm(self) -> None:
        """classify_intent_node stores the parsed LLM label."""
        ctx = _ctx(llm=FakeLLM(["lookup"]))
        out = classify_intent_node(ctx, empty_state("segment id 1"))
        assert out["intent"] == "lookup"

    def test_hybrid_search_reads_store(self) -> None:
        """Hybrid search returns upserted points."""
        store = FakeVectorStore()
        store.upsert(
            COLLECTION_DOMAIN_KNOWLEDGE,
            [
                UpsertRecord(
                    point_id="a",
                    text="cookie reach graph estimate",
                    dense_vector=HashEmbedder().embed_query("cookie reach graph estimate"),
                    filename="reach_metrics.md",
                    section="cookie_reach",
                    token_count=4,
                )
            ],
        )
        # pad vector length
        rec = store._points[COLLECTION_DOMAIN_KNOWLEDGE]["a"]
        if len(rec.dense_vector) != EMBEDDING_DIM:
            rec.dense_vector = rec.dense_vector[:EMBEDDING_DIM]
        ctx = _ctx(store=store)
        out = run_hybrid_search_node(ctx, empty_state("cookie reach"))
        assert "vector_results" in out

    def test_threshold_filters(self) -> None:
        """threshold_gate drops low scores when threshold is high."""
        ctx = _ctx()
        ctx.settings = Settings(
            google_api_key="fake-api-key",
            bigquery_project="p",
            similarity_threshold=0.9,
        )
        state = empty_state("q")
        state["vector_results"] = [_hit(0.2)]
        out = threshold_gate_node(ctx, state)
        assert out["threshold_failed"] is True
        assert out["vector_results"] == []

    def test_rerank_limits(self) -> None:
        """rerank_results_node keeps at most top_k_final hits."""
        ctx = _ctx()
        state = empty_state("activation")
        state["vector_results"] = [_hit(0.4), _hit(0.9), _hit(0.1), _hit(0.2)]
        out = rerank_results_node(ctx, state)
        assert len(out["vector_results"]) <= 3

    def test_text2sql_executes(self) -> None:
        """run_text2sql_node validates SELECT and fills sql_results."""
        llm = FakeLLM(["SELECT cookie_reach FROM bestsellers_segments LIMIT 10"])
        ctx = _ctx(llm=llm)
        out = run_text2sql_node(ctx, empty_state("top cookie reach"))
        assert out["sql_used"] is not None
        assert out["sql_results"]

    def test_synthesize_fallback(self) -> None:
        """Empty retrieval and SQL produce the grounded fallback."""
        ctx = _ctx()
        state = empty_state("unknown topic xyz")
        state["threshold_failed"] = True
        state["sql_results"] = []
        out = synthesize_node(ctx, state)
        assert "sufficient grounded information" in out["final_answer"]
        assert out["confidence"] == 0.0
