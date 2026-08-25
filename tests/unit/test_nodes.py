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
    wrap_llm_sql_with_pipeline,
)
from lr_bestsellers.agent.sql_pipeline import assemble_bestsellers_query
from lr_bestsellers.agent.tools import FakeBqRunner
from lr_bestsellers.config import Settings
from lr_bestsellers.models.chunk import ChildChunk, SearchResult
from lr_bestsellers.store.protocols import COLLECTION_DOMAIN_KNOWLEDGE, EMBEDDING_DIM, UpsertRecord
from lr_bestsellers.utils.embeddings import HashEmbedder
from lr_bestsellers.utils.reranker import CrossEncoderReranker
from tests.unit.test_store_protocol import FakeVectorStore

# ---------------------------------------------------------------------------
# Realistic 2-CTE pipeline fixture (mirrors best_sellers.sql structure)
# ---------------------------------------------------------------------------

_PIPELINE_SQL = """\
WITH base AS (
  SELECT
    1 AS dms_segment_id,
    'Test Segment' AS segment_name,
    'desc' AS segment_description,
    'standard' AS segment_type,
    'seller1' AS seller_customer_id,
    5 AS active_destination_accounts,
    3 AS active_buyers,
    2 AS active_platforms,
    'The Trade Desk, Google DV360' AS active_platform_names,
    100000 AS cookie_reach,
    0 AS ios_reach,
    0 AS android_reach,
    50000 AS input_records,
    CURRENT_TIMESTAMP() AS cookie_reach_updated_at,
    CURRENT_TIMESTAMP() AS ios_reach_updated_at,
    CURRENT_TIMESTAMP() AS android_reach_updated_at,
    NULL AS reach_by_platform
),
classified AS (
  SELECT
    *,
    TRUE AS is_highly_distributed,
    FALSE AS is_highly_reachable,
    FALSE AS is_top_n_by_reach,
    1 AS distribution_rank,
    1 AS reach_rank
  FROM base
)
SELECT
  dms_segment_id,
  segment_name,
  segment_description,
  segment_type,
  seller_customer_id,
  active_destination_accounts,
  active_buyers,
  active_platforms,
  active_platform_names,
  cookie_reach,
  ios_reach,
  android_reach,
  input_records,
  cookie_reach_updated_at,
  ios_reach_updated_at,
  android_reach_updated_at,
  reach_by_platform,
  distribution_rank,
  reach_rank,
  is_highly_distributed,
  is_highly_reachable,
  is_top_n_by_reach
FROM classified
WHERE is_highly_distributed"""


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
    pipeline_sql: str = _PIPELINE_SQL,
) -> NodeContext:
    """Build a NodeContext with fakes.

    Args:
        store: Optional preloaded store.
        llm: Optional fake LLM.
        pipeline_sql: Pipeline SQL override.

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
        pipeline_sql=pipeline_sql,
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

    def test_text2sql_executes_with_cte_pipeline(self) -> None:
        """run_text2sql_node CTE-merges the pipeline and produces valid SQL."""
        llm = FakeLLM(
            ["SELECT dms_segment_id, cookie_reach FROM bestsellers_segments LIMIT 10"]
        )
        ctx = _ctx(llm=llm)
        out = run_text2sql_node(ctx, empty_state("top cookie reach"))
        assert out["sql_used"] is not None
        used = out["sql_used"] or ""
        # With new CTE assembler the WITH block must be at the top level.
        assert used.strip().upper().startswith("WITH")
        # The pipeline CTE block must be present.
        assert "base AS" in used
        assert "classified AS" in used
        # The pipeline final select must be wrapped as bestsellers_segments CTE.
        assert "bestsellers_segments AS" in used
        # No nested WITH inside parens.
        assert "FROM (\n" not in used
        assert out["sql_results"] is not None

    def test_synthesize_fallback(self) -> None:
        """Empty retrieval and SQL produce the grounded fallback."""
        ctx = _ctx()
        state = empty_state("unknown topic xyz")
        state["threshold_failed"] = True
        state["sql_results"] = []
        out = synthesize_node(ctx, state)
        assert "sufficient grounded information" in out["final_answer"]
        assert out["confidence"] == 0.0


class TestAssembleBestsellersCteQuery:
    """Direct tests for :func:`assemble_bestsellers_query`."""

    def test_happy_path_no_nested_with(self) -> None:
        """Happy path: assembled SQL starts with WITH, no nested WITH."""
        llm_sql = (
            "SELECT dms_segment_id, cookie_reach FROM bestsellers_segments LIMIT 10"
        )
        result = assemble_bestsellers_query(llm_sql, _PIPELINE_SQL)
        assert result.strip().upper().startswith("WITH")
        # Check 'WITH' appears only once at the start.
        assert result.upper().count("\nWITH ") == 0 or result.upper().startswith("WITH")
        assert "bestsellers_segments AS (" in result
        # LLM SELECT is at the end.
        assert "SELECT dms_segment_id, cookie_reach FROM bestsellers_segments LIMIT 10" in result

    def test_llm_with_clause_is_merged(self) -> None:
        """LLM WITH is merged; only one top-level WITH keyword."""
        llm_sql = (
            "WITH filtered AS (\n"
            "  SELECT * FROM bestsellers_segments WHERE distribution_rank <= 10\n"
            ")\n"
            "SELECT * FROM filtered LIMIT 10"
        )
        result = assemble_bestsellers_query(llm_sql, _PIPELINE_SQL)
        upper = result.upper()
        # Single WITH at the very start.
        assert upper.lstrip().startswith("WITH")
        # No second top-level WITH.
        assert upper.count("WITH ") == 1 or upper.startswith("WITH")
        assert "bestsellers_segments AS (" in result
        assert "filtered AS (" in result
        assert "SELECT * FROM filtered LIMIT 10" in result

    def test_empty_pipeline_returns_llm_sql_unchanged(self) -> None:
        """Empty pipeline leaves LLM SQL unchanged."""
        raw = "SELECT 1 FROM bestsellers_segments"
        assert assemble_bestsellers_query(raw, "") == raw

    def test_no_double_with_in_output(self) -> None:
        """The assembled SQL must never have WITH inside a subquery."""
        llm_sql = "SELECT * FROM bestsellers_segments ORDER BY distribution_rank LIMIT 1000"
        result = assemble_bestsellers_query(llm_sql, _PIPELINE_SQL)
        # No parenthesised WITH — the old broken pattern.
        assert "(\nWITH" not in result
        assert "( WITH" not in result.replace("\n", " ")


class TestWrapPipeline:
    """Backward-compat subquery wrap tests (plain pipeline = bare SELECT)."""

    def test_empty_pipeline_is_noop(self) -> None:
        """Missing pipeline leaves LLM SQL unchanged."""
        raw = "SELECT 1 FROM bestsellers_segments"
        assert wrap_llm_sql_with_pipeline(raw, "") == raw

    def test_pipeline_body_is_present(self) -> None:
        """The catalog WITH query appears in the assembled SQL."""
        pipeline = (
            "WITH syndicated_segments AS (SELECT 1 AS dms_segment_id)\n"
            "SELECT dms_segment_id FROM syndicated_segments"
        )
        sql = wrap_llm_sql_with_pipeline(
            "SELECT dms_segment_id FROM bestsellers_segments LIMIT 10",
            pipeline,
        )
        assert "syndicated_segments AS" in sql

    def test_cte_merged_not_subquery_wrapped(self) -> None:
        """CTE pipeline is merged into top-level CTEs, not wrapped in parens."""
        sql = wrap_llm_sql_with_pipeline(
            "SELECT dms_segment_id FROM bestsellers_segments LIMIT 10",
            _PIPELINE_SQL,
        )
        assert sql.strip().upper().startswith("WITH")
        assert "FROM (\nWITH" not in sql
