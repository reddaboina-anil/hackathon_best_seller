"""LangGraph ``AgentState`` and compiled graph factory."""

from __future__ import annotations

from typing import Literal, Protocol, TypedDict, cast

import structlog
from langgraph.graph import END, START, StateGraph
from qdrant_client import QdrantClient

from lr_bestsellers.agent.nodes import GeminiLLM, NodeContext, make_node_map
from lr_bestsellers.agent.tools import BigQueryRunner, FakeBqRunner
from lr_bestsellers.config import Settings
from lr_bestsellers.guardrails import SqlChainValidator
from lr_bestsellers.hooks.callbacks import SegmentIntelligenceCallbackHandler
from lr_bestsellers.hooks.metrics import get_metrics
from lr_bestsellers.ingestion.bq_fetcher import build_bigquery_client
from lr_bestsellers.models.chunk import SearchResult
from lr_bestsellers.models.query import QueryIntent, QueryResponse, SourceCitation, SqlRow
from lr_bestsellers.store.qdrant import QdrantRepository
from lr_bestsellers.utils.embeddings import GoogleEmbedder, HashEmbedder
from lr_bestsellers.utils.reranker import CrossEncoderReranker

log = structlog.get_logger(__name__)

RouteName = Literal["vector", "sql", "both"]


class AgentState(TypedDict, total=False):
    """Immutable LangGraph state. Nodes return partial dicts only.

    Attributes:
        query: User question.
        intent: Classified routing intent.
        vector_results: Hybrid search hits after rerank/threshold.
        sql_results: BigQuery rows.
        final_answer: Synthesised answer text.
        sources: Citations attached to the answer.
        sql_used: SQL that was executed, if any.
        confidence: Self-assessed confidence in ``[0, 1]``.
        threshold_failed: True when no vector hit cleared the gate.
    """

    query: str
    intent: QueryIntent
    vector_results: list[SearchResult]
    sql_results: list[SqlRow]
    final_answer: str
    sources: list[SourceCitation]
    sql_used: str | None
    confidence: float
    threshold_failed: bool


def route_intent(state: AgentState) -> RouteName:
    """Map classified intent onto a graph branch.

    Args:
        state: Current state (must include ``intent`` after classify).

    Returns:
        ``vector``, ``sql``, or ``both``.
    """
    intent = state.get("intent") or "vague"
    if intent in ("conceptual", "lookup"):
        return "vector"
    if intent == "analytics":
        return "sql"
    return "both"


def empty_state(query: str) -> AgentState:
    """Build the initial graph state for ``query``.

    Args:
        query: User question.

    Returns:
        Fully keyed ``AgentState``.
    """
    return {
        "query": query,
        "intent": "vague",
        "vector_results": [],
        "sql_results": [],
        "final_answer": "",
        "sources": [],
        "sql_used": None,
        "confidence": 0.0,
        "threshold_failed": False,
    }


class CompiledGraph(Protocol):
    """Minimal compiled LangGraph surface used by :func:`run_query`."""

    def invoke(self, input: AgentState, config: dict[str, object] | None = None) -> AgentState:
        """Run the graph.

        Args:
            input: Initial state.
            config: Optional runnable config (callbacks).

        Returns:
            Final state.
        """
        ...


def compile_graph(ctx: NodeContext) -> CompiledGraph:
    """Compile the classify → retrieve/SQL → synthesize graph.

    Args:
        ctx: Injected node collaborators.

    Returns:
        Compiled LangGraph runnable.
    """
    nodes = make_node_map(ctx)
    graph = StateGraph(AgentState)
    graph.add_node("classify_intent", nodes["classify_intent"])
    graph.add_node("run_hybrid_search", nodes["run_hybrid_search"])
    graph.add_node("rerank_results", nodes["rerank_results"])
    graph.add_node("threshold_gate", nodes["threshold_gate"])
    graph.add_node("run_text2sql", nodes["run_text2sql"])
    graph.add_node("synthesize", nodes["synthesize"])
    graph.add_edge(START, "classify_intent")
    graph.add_conditional_edges(
        "classify_intent",
        route_intent,
        {
            "vector": "run_hybrid_search",
            "sql": "run_text2sql",
            "both": "run_hybrid_search",
        },
    )
    graph.add_edge("run_hybrid_search", "rerank_results")
    graph.add_edge("rerank_results", "threshold_gate")
    graph.add_conditional_edges(
        "threshold_gate",
        _after_vector,
        {
            "sql": "run_text2sql",
            "synthesize": "synthesize",
        },
    )
    graph.add_edge("run_text2sql", "synthesize")
    graph.add_edge("synthesize", END)
    compiled = graph.compile()
    return cast(CompiledGraph, compiled)


def _after_vector(state: AgentState) -> Literal["sql", "synthesize"]:
    """After the vector path, optionally still run SQL for mixed/vague/analytics.

    Analytics never enters this function (it skipped hybrid search). Mixed and
    vague enter hybrid first then SQL. Conceptual/lookup go to synthesize.

    Args:
        state: Current state.

    Returns:
        Next node key.
    """
    intent = state.get("intent") or "vague"
    if intent in ("mixed", "vague"):
        return "sql"
    return "synthesize"


def build_node_context(
    settings: Settings,
    *,
    store: QdrantRepository | None = None,
    llm: GeminiLLM | None = None,
    bq: BigQueryRunner | FakeBqRunner | None = None,
) -> NodeContext:
    """Construct production (or test-overridden) node collaborators.

    Args:
        settings: Application settings.
        store: Optional injected Qdrant repository.
        llm: Optional injected LLM.
        bq: Optional injected BQ runner.

    Returns:
        Ready ``NodeContext``.
    """
    api_key = settings.google_api_key.get_secret_value()
    if store is None:
        q_key = settings.qdrant_api_key.get_secret_value() if settings.qdrant_api_key else None
        store = QdrantRepository(QdrantClient(url=settings.qdrant_url, api_key=q_key))
        try:
            store.ensure_collections()
        except Exception as exc:
            log.error("graph.qdrant_unavailable", error=str(exc))
    if api_key.startswith("your-") or api_key in {"fake-api-key", "test-key"}:
        from lr_bestsellers.agent.nodes import FakeLLM

        embedder = HashEmbedder()
        active_llm = llm or FakeLLM(
            [
                "analytics",
                (
                    "SELECT dms_segment_id, segment_name, cookie_reach, reach_rank "
                    "FROM bestsellers_segments ORDER BY cookie_reach DESC LIMIT 10"
                ),
                "The top segment by cookie reach in the current result set is Auto "
                "Intenders with cookie_reach 1000000 and reach_rank 1. "
                "[Source: BigQuery]",
            ]
        )
        active_bq: BigQueryRunner | FakeBqRunner = bq or FakeBqRunner()
    else:
        embedder = GoogleEmbedder(api_key=api_key, model=settings.embedding_model)
        active_llm = llm or GeminiLLM(api_key=api_key, model=settings.llm_model)
        if bq is not None:
            active_bq = bq
        else:
            try:
                active_bq = BigQueryRunner(build_bigquery_client(settings))
            except Exception as exc:
                log.error("graph.bq_unavailable", error=str(exc))
                active_bq = FakeBqRunner()
    return NodeContext(
        settings=settings,
        store=store,
        embedder=embedder,
        reranker=CrossEncoderReranker(),
        llm=active_llm,
        bq=active_bq,
        sql_validator=SqlChainValidator(),
    )


def run_query(text: str, ctx: NodeContext) -> QueryResponse:
    """Invoke the compiled graph and map state to ``QueryResponse``.

    Args:
        text: User question.
        ctx: Node collaborators.

    Returns:
        Grounded ``QueryResponse``.
    """
    handler = SegmentIntelligenceCallbackHandler(get_metrics())
    graph = compile_graph(ctx)
    get_metrics().incr("queries.total")
    raw: AgentState = graph.invoke(
        empty_state(text),
        config={"callbacks": [handler]},
    )
    return QueryResponse(
        answer=raw.get("final_answer") or "",
        sources=list(raw.get("sources") or []),
        sql_used=raw.get("sql_used"),
        confidence=float(raw.get("confidence") or 0.0),
        intent=raw.get("intent") or "vague",
    )
