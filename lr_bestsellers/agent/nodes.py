"""Pure LangGraph node functions (closures over :class:`NodeContext`)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Protocol, cast, runtime_checkable

import structlog
from pydantic import BaseModel
from pydantic import Field as pydantic_field

from lr_bestsellers.agent.prompts import (
    CLASSIFY_INTENT_PROMPT,
    GROUNDING_FALLBACK,
    SQL_RETRY_PROMPT,
    SYNTHESIZE_PROMPT,
    TEXT2SQL_PROMPT,
    build_platform_hint,
)
from lr_bestsellers.agent.sql_pipeline import assemble_bestsellers_query
from lr_bestsellers.agent.tools import (
    BqRunnerProtocol,
    HybridSearchInput,
    format_search_results,
    run_hybrid_search,
)
from lr_bestsellers.config import DEFAULT_LLM_MODEL, Settings
from lr_bestsellers.models.query import BqQueryRequest, QueryIntent, SourceCitation, SqlRow
from lr_bestsellers.store.protocols import (
    COLLECTION_DOMAIN_KNOWLEDGE,
    COLLECTION_GLOSSARY,
    COLLECTION_PLATFORM_NAMES,
    COLLECTION_SEGMENT_CATALOG,
    HybridSearchRequest,
    VectorStoreProtocol,
)
from lr_bestsellers.utils.embeddings import EmbedderProtocol
from lr_bestsellers.utils.reranker import CrossEncoderReranker, RerankRequest

if TYPE_CHECKING:
    from lr_bestsellers.agent.graph import AgentState

log = structlog.get_logger(__name__)

_VALID_INTENTS: frozenset[str] = frozenset({"analytics", "conceptual", "lookup", "mixed", "vague"})
_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
DEFAULT_BESTSELLERS_SQL_PATH: Final[Path] = _REPO_ROOT / "best_sellers.sql"


@runtime_checkable
class LLMProtocol(Protocol):
    """Minimal completion interface used by nodes."""

    def complete(self, prompt: str) -> str:
        """Return model text for ``prompt``.

        Args:
            prompt: Fully rendered prompt.

        Returns:
            Model output string.
        """
        ...

    def complete_sql(self, prompt: str) -> str:
        """Return a SQL string via structured output (no markdown fences possible).

        Args:
            prompt: Text2SQL prompt.

        Returns:
            Raw SQL string extracted from the model's structured response.
        """
        ...


@runtime_checkable
class SqlValidatorProtocol(Protocol):
    """Optional SQL rewrite/validate hook (guardrails in M5)."""

    def validate(self, sql: str) -> str:
        """Return SQL allowed to run (possibly rewritten).

        Args:
            sql: Candidate statement.

        Returns:
            Executable SQL.

        Raises:
            SQLGuardrailError: When the statement is rejected.
        """
        ...


class PassthroughSqlValidator:
    """Accepts SQL unchanged after a SELECT/WITH prefix check."""

    def validate(self, sql: str) -> str:
        """Strip fences and require SELECT or WITH.

        Args:
            sql: Raw LLM SQL.

        Returns:
            Cleaned SQL.

        Raises:
            ValueError: When the statement is not SELECT/WITH.
        """
        cleaned = strip_sql_fences(sql)
        head = cleaned.lstrip().split(None, 1)[0].upper() if cleaned.strip() else ""
        if head not in {"SELECT", "WITH"}:
            raise ValueError("SQL must start with SELECT or WITH")
        return cleaned


class FakeLLM:
    """Deterministic LLM for unit tests.

    Args:
        responses: FIFO list of completions; the last value repeats.
    """

    def __init__(self, responses: list[str] | None = None) -> None:
        """Store canned responses.

        Args:
            responses: Completions to pop from the front.
        """
        self._responses = list(responses or ["conceptual"])
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        """Return the next canned completion.

        Args:
            prompt: Ignored except for recording.

        Returns:
            Next or last canned string.
        """
        self.prompts.append(prompt)
        if len(self._responses) == 1:
            return self._responses[0]
        return self._responses.pop(0)

    def complete_sql(self, prompt: str) -> str:
        """Return the next canned SQL completion (delegates to :meth:`complete`).

        Args:
            prompt: Ignored except for recording.

        Returns:
            Next or last canned string.
        """
        return self.complete(prompt)


class _SqlOutput(BaseModel):
    """Structured response schema for SQL generation."""

    sql: str = pydantic_field(description="Valid BigQuery Standard SQL SELECT or WITH statement.")


class GeminiLLM:
    """Gemini chat wrapper.

    Args:
        api_key: Google AI Studio key.
        model: Gemini chat model id.
    """

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_LLM_MODEL,
    ) -> None:
        """Create the chat model.

        Args:
            api_key: API key.
            model: Chat model id (from ``Settings.llm_model``).
        """
        from langchain_google_genai import ChatGoogleGenerativeAI

        self._model = ChatGoogleGenerativeAI(
            model=model,
            google_api_key=api_key,
            temperature=0.0,
        )
        # Structured output variant: forces JSON {"sql": "..."} — no fences possible.
        self._sql_model = self._model.with_structured_output(_SqlOutput)

    def complete(self, prompt: str) -> str:
        """Invoke Gemini and return text content.

        Args:
            prompt: User/system concatenated prompt.

        Returns:
            Response text.
        """
        message = self._model.invoke(prompt)
        content = message.content
        if isinstance(content, str):
            return content
        return str(content)

    def complete_sql(self, prompt: str) -> str:
        """Invoke Gemini with structured output to extract a clean SQL string.

        The model is constrained by the API to return ``{"sql": "..."}`` so
        markdown fences, preambles, and explanations are structurally impossible.

        Args:
            prompt: Text2SQL prompt.

        Returns:
            Raw SQL string.

        Raises:
            EmbeddingError: Propagates any API error from the structured call.
        """
        result = self._sql_model.invoke(prompt)
        if isinstance(result, _SqlOutput):
            return result.sql
        # Fallback: result is a dict when the model returns partial JSON
        if isinstance(result, dict):
            return str(result.get("sql", ""))
        return str(result)


class NodeContext:
    """Injected collaborators for node closures.

    Args:
        settings: App settings.
        store: Vector store.
        embedder: Dense embedder.
        reranker: Result reranker.
        llm: Completion model.
        bq: BigQuery runner.
        sql_validator: SQL check/rewrite hook.
        pipeline_sql: Live ``best_sellers.sql`` body injected as a CTE.
        platform_resolver: Optional platform name resolver; when ``None`` the
            node falls back to no-hint mode.
    """

    def __init__(
        self,
        settings: Settings,
        store: VectorStoreProtocol,
        embedder: EmbedderProtocol,
        reranker: CrossEncoderReranker,
        llm: LLMProtocol,
        bq: BqRunnerProtocol,
        sql_validator: SqlValidatorProtocol | None = None,
        pipeline_sql: str = "",
        platform_resolver: object | None = None,
    ) -> None:
        """Store collaborators.

        Args:
            settings: App settings.
            store: Vector store.
            embedder: Dense embedder.
            reranker: Result reranker.
            llm: Completion model.
            bq: BigQuery runner.
            sql_validator: Optional SQL validator.
            pipeline_sql: Contents of ``best_sellers.sql`` without a trailing
                semicolon. Empty skips CTE wrapping (unit tests).
            platform_resolver: Optional :class:`PlatformResolver` instance.
        """
        self.settings = settings
        self.store = store
        self.embedder = embedder
        self.reranker = reranker
        self.llm = llm
        self.bq = bq
        self.sql_validator: SqlValidatorProtocol = sql_validator or PassthroughSqlValidator()
        self.pipeline_sql = pipeline_sql
        self.platform_resolver = platform_resolver


def load_bestsellers_pipeline(sql_path: Path | None = None) -> str:
    """Read ``best_sellers.sql`` for query-time execution.

    Args:
        sql_path: Override path. Defaults to the repo-root SQL file.

    Returns:
        Pipeline SQL with no trailing semicolon, or ``""`` if the file
        cannot be read.
    """
    path = sql_path or DEFAULT_BESTSELLERS_SQL_PATH
    try:
        body = path.read_text(encoding="utf-8").strip().rstrip(";")
    except OSError as exc:
        log.error("sql.pipeline_read_failed", path=str(path), error=str(exc))
        return ""
    if not body:
        log.error("sql.pipeline_empty", path=str(path))
        return ""
    log.info("sql.pipeline_loaded", path=str(path), chars=len(body))
    return body


def wrap_llm_sql_with_pipeline(llm_sql: str, pipeline_sql: str) -> str:
    """Bind model SQL to ``best_sellers.sql`` as a derived table.

    Args:
        llm_sql: SELECT/WITH generated by the model.
        pipeline_sql: Body of ``best_sellers.sql``.

    Returns:
        Executable SQL. Unchanged when ``pipeline_sql`` is empty.
    """
    return assemble_bestsellers_query(llm_sql, pipeline_sql)


def strip_sql_fences(sql: str) -> str:
    """Remove markdown code fences from LLM SQL.

    Args:
        sql: Raw model output.

    Returns:
        SQL without surrounding fences.
    """
    text = sql.strip()
    text = re.sub(r"^```(?:sql)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def keyword_intent(query: str) -> QueryIntent:
    """Heuristic intent classifier used as LLM fallback.

    Args:
        query: User question.

    Returns:
        A ``QueryIntent`` label.
    """
    lowered = query.lower()
    strong_analytics = any(
        token in lowered
        for token in (
            "top ",
            "how many",
            "compare",
            "highest",
            "lowest",
            "rank",
            "count ",
        )
    )
    conceptual_hits = any(
        token in lowered
        for token in ("what is", "what's", "how does", "explain", "define", "meaning of")
    )
    lookup_hits = any(token in lowered for token in ("segment id", "dms_segment", "named segment"))
    if conceptual_hits and strong_analytics:
        return "mixed"
    if lookup_hits and not conceptual_hits:
        return "lookup"
    if conceptual_hits:
        return "conceptual"
    if strong_analytics or "reach" in lowered:
        return "analytics"
    return "vague"


def parse_intent(raw: str, query: str) -> QueryIntent:
    """Parse an LLM intent label with keyword fallback.

    Args:
        raw: Raw LLM output.
        query: Original query for fallback.

    Returns:
        Valid ``QueryIntent``.
    """
    token = raw.strip().split()[0].lower().strip(".,:;\"'") if raw.strip() else ""
    if token in _VALID_INTENTS:
        return cast(QueryIntent, token)
    return keyword_intent(query)


def classify_intent_node(ctx: NodeContext, state: AgentState) -> dict[str, Any]:
    """Classify user intent.

    Args:
        ctx: Injected collaborators.
        state: Current graph state.

    Returns:
        Partial state with ``intent``.
    """
    query = state["query"]
    log.info("node.start", node="classify_intent", query=query[:50])
    prompt = CLASSIFY_INTENT_PROMPT.format(query=query)
    try:
        raw = ctx.llm.complete(prompt)
        intent = parse_intent(raw, query)
    except Exception as exc:
        log.error("node.error", node="classify_intent", error=str(exc))
        intent = keyword_intent(query)
    log.info("node.complete", node="classify_intent", intent=intent)
    return {"intent": intent}


def run_hybrid_search_node(ctx: NodeContext, state: AgentState) -> dict[str, Any]:
    """Search glossary, domain knowledge, and segment catalog.

    Args:
        ctx: Injected collaborators.
        state: Current graph state.

    Returns:
        Partial state with ``vector_results``.
    """
    query = state["query"]
    log.info("node.start", node="hybrid_search", query=query[:50])
    top_k = ctx.settings.top_k_retrieval
    combined = []
    for collection in (
        COLLECTION_GLOSSARY,
        COLLECTION_DOMAIN_KNOWLEDGE,
        COLLECTION_SEGMENT_CATALOG,
    ):
        try:
            hits = run_hybrid_search(
                ctx.store,
                ctx.embedder,
                HybridSearchInput(query=query, collection=collection, top_k=top_k),
            )
        except Exception as exc:
            log.error("node.error", node="hybrid_search", collection=collection, error=str(exc))
            hits = []
        combined.extend(hits)
    combined.sort(key=lambda item: item.score, reverse=True)
    log.info("node.complete", node="hybrid_search", hits=len(combined))
    return {"vector_results": combined[:top_k]}


def rerank_results_node(ctx: NodeContext, state: AgentState) -> dict[str, Any]:
    """Rerank hybrid hits down to ``top_k_final``.

    Args:
        ctx: Injected collaborators.
        state: Current graph state.

    Returns:
        Partial state with reranked ``vector_results``.
    """
    log.info("node.start", node="rerank_node")
    ranked = ctx.reranker.rerank(
        RerankRequest(
            query=state["query"],
            results=list(state.get("vector_results") or []),
            top_k=ctx.settings.top_k_final,
        )
    )
    log.info("node.complete", node="rerank_node", hits=len(ranked))
    return {"vector_results": ranked}


def threshold_gate_node(ctx: NodeContext, state: AgentState) -> dict[str, Any]:
    """Drop hits below ``similarity_threshold``.

    Args:
        ctx: Injected collaborators.
        state: Current graph state.

    Returns:
        Partial state with filtered results and ``threshold_failed``.
    """
    threshold = ctx.settings.similarity_threshold
    kept = [item for item in (state.get("vector_results") or []) if item.score >= threshold]
    failed = len(kept) == 0
    log.info("node.complete", node="threshold_gate", kept=len(kept), failed=failed)
    return {"vector_results": kept, "threshold_failed": failed}


_PLATFORM_FILTER_RE: Final[re.Pattern[str]] = re.compile(
    r"\bactive_platform_names\b",
    re.IGNORECASE,
)


def _has_platform_filter(sql: str) -> bool:
    """Return ``True`` when ``sql`` filters on ``active_platform_names``.

    Args:
        sql: Assembled SQL statement.

    Returns:
        Whether a platform filter is present.
    """
    return bool(_PLATFORM_FILTER_RE.search(sql))


def _fetch_platform_list(store: VectorStoreProtocol, embedder: EmbedderProtocol) -> list[str]:
    """Return all canonical platform names stored in Qdrant.

    Args:
        store: Vector store.
        embedder: Dense embedder (provides query vector).

    Returns:
        Sorted list of canonical platform name strings.
    """
    try:
        if not store.collection_exists(COLLECTION_PLATFORM_NAMES):
            return []
        hits = store.hybrid_search(
            HybridSearchRequest(
                collection=COLLECTION_PLATFORM_NAMES,
                query_text="platform",
                dense_vector=embedder.embed_query("platform"),
                top_k=100,
            )
        )
        return sorted({h.chunk.text.strip() for h in hits if h.chunk.text.strip()})
    except Exception as exc:
        log.warning("nodes.platform_list_fetch_failed", error=str(exc))
        return []


def run_text2sql_node(ctx: NodeContext, state: AgentState) -> dict[str, Any]:
    """Generate, validate, and execute BigQuery SQL.

    Uses :meth:`LLMProtocol.complete_sql` which enforces a structured-output
    schema at the API level — markdown fences and preambles are impossible.

    Round 1: resolve platform hint → build prompt → generate SQL → assemble
    (CTE merge) → validate → execute.

    Round 2 (single retry): if Round 1 returns 0 rows *and* the SQL contains
    a platform filter, fetch canonical platform names from Qdrant, inject
    them into :data:`SQL_RETRY_PROMPT`, regenerate, assemble, validate,
    and execute once more. Result is returned regardless.

    Args:
        ctx: Injected collaborators.
        state: Current graph state.

    Returns:
        Partial state with ``sql_used`` and ``sql_results``.
    """
    query = state["query"]
    log.info("node.start", node="run_text2sql", query=query[:50])

    # Resolve platform hint via PlatformResolver (may return None).
    hint: str | None = None
    resolver = ctx.platform_resolver
    if resolver is not None:
        try:
            resolve_fn = getattr(resolver, "resolve", None)
            if callable(resolve_fn):
                hint = resolve_fn(query)
        except Exception as exc:
            log.warning("node.platform_resolve_failed", error=str(exc))

    platform_hint_block = build_platform_hint(hint)
    prompt = TEXT2SQL_PROMPT.format(query=query, platform_hint=platform_hint_block)
    raw_sql = ctx.llm.complete_sql(prompt)
    log.info("node.sql_generated", node="run_text2sql", sql=raw_sql[:200])
    bound_sql = wrap_llm_sql_with_pipeline(raw_sql, ctx.pipeline_sql)
    log.info("node.sql_bound", node="run_text2sql", sql=bound_sql[:200])
    sql: str | None = None
    rows: list[SqlRow] = []
    try:
        sql = ctx.sql_validator.validate(bound_sql)
        log.info("node.sql_validated", node="run_text2sql", sql=sql[:200])
        rows = ctx.bq.execute(BqQueryRequest(sql=sql))
    except Exception as exc:
        log.error(
            "node.error",
            node="run_text2sql",
            error=str(exc),
            sql=sql or bound_sql,
        )
        return {"sql_used": None, "sql_results": []}

    log.info("node.complete", node="run_text2sql", rows=len(rows), round=1)

    # Zero-row retry: only when the query had a platform filter.
    if len(rows) == 0 and sql and _has_platform_filter(sql):
        log.info("node.retry_start", node="run_text2sql", reason="zero_rows_platform_filter")
        platform_used = hint or "(unknown)"
        platform_names = _fetch_platform_list(ctx.store, ctx.embedder)
        platform_list_str = (
            "\n".join(f"  - {p}" for p in platform_names) if platform_names else "  (no platforms indexed)"
        )
        retry_prompt = SQL_RETRY_PROMPT.format(
            query=query,
            platform_used=platform_used,
            platform_list=platform_list_str,
        )
        try:
            retry_raw = ctx.llm.complete_sql(retry_prompt)
            log.info("node.retry_sql_generated", sql=retry_raw[:200])
            retry_bound = wrap_llm_sql_with_pipeline(retry_raw, ctx.pipeline_sql)
            retry_sql = ctx.sql_validator.validate(retry_bound)
            rows = ctx.bq.execute(BqQueryRequest(sql=retry_sql))
            sql = retry_sql
            log.info("node.retry_complete", node="run_text2sql", rows=len(rows), round=2)
        except Exception as exc:
            log.warning(
                "node.retry_failed",
                node="run_text2sql",
                error=str(exc),
            )
            # Keep Round 1 sql; rows stay empty.

    return {"sql_used": sql, "sql_results": rows}


def synthesize_node(ctx: NodeContext, state: AgentState) -> dict[str, Any]:
    """Synthesize a cited answer from retrieval and SQL evidence.

    Args:
        ctx: Injected collaborators.
        state: Current graph state.

    Returns:
        Partial state with ``final_answer``, ``sources``, and ``confidence``.
    """
    log.info("node.start", node="synthesize")
    vector_results = list(state.get("vector_results") or [])
    sql_results: list[SqlRow] = list(state.get("sql_results") or [])
    sql_used = state.get("sql_used")
    threshold_failed = bool(state.get("threshold_failed"))
    if threshold_failed and not sql_results:
        log.info("node.complete", node="synthesize", fallback=True)
        return {
            "final_answer": GROUNDING_FALLBACK,
            "sources": [],
            "confidence": 0.0,
        }
    context = format_search_results(vector_results)
    sql_lines = "\n".join(str(row.fields) for row in sql_results) or "(none)"
    prompt = SYNTHESIZE_PROMPT.format(
        context=context,
        sql_used=sql_used or "(none)",
        sql_rows=sql_lines,
        query=state["query"],
    )
    answer = ctx.llm.complete(prompt)
    sources: list[SourceCitation] = []
    for item in vector_results:
        sources.append(
            SourceCitation(
                source=item.chunk.filename,
                text=(item.parent_text or item.chunk.text)[:500],
                score=item.score,
            )
        )
    if sql_used:
        sources.append(
            SourceCitation(
                source="BigQuery",
                text=sql_lines[:500],
                score=1.0,
            )
        )
    confidence = 0.5
    if vector_results:
        confidence = max(item.score for item in vector_results)
    if sql_results:
        confidence = max(confidence, 0.85)
    log.info("node.complete", node="synthesize", confidence=confidence)
    return {
        "final_answer": answer.strip(),
        "sources": sources,
        "confidence": min(1.0, confidence),
    }


def make_node_map(ctx: NodeContext) -> dict[str, Any]:
    """Bind all node callables to ``ctx``.

    Args:
        ctx: Injected collaborators.

    Returns:
        Mapping of graph node name to callable.
    """

    def classify_intent(state: AgentState) -> dict[str, Any]:
        """Classify intent node.

        Args:
            state: Graph state.

        Returns:
            Partial update.
        """
        return classify_intent_node(ctx, state)

    def run_hybrid_search(state: AgentState) -> dict[str, Any]:
        """Hybrid search node.

        Args:
            state: Graph state.

        Returns:
            Partial update.
        """
        return run_hybrid_search_node(ctx, state)

    def rerank_results(state: AgentState) -> dict[str, Any]:
        """Rerank node.

        Args:
            state: Graph state.

        Returns:
            Partial update.
        """
        return rerank_results_node(ctx, state)

    def threshold_gate(state: AgentState) -> dict[str, Any]:
        """Threshold gate node.

        Args:
            state: Graph state.

        Returns:
            Partial update.
        """
        return threshold_gate_node(ctx, state)

    def run_text2sql(state: AgentState) -> dict[str, Any]:
        """Text2SQL node.

        Args:
            state: Graph state.

        Returns:
            Partial update.
        """
        return run_text2sql_node(ctx, state)

    def synthesize(state: AgentState) -> dict[str, Any]:
        """Synthesize node.

        Args:
            state: Graph state.

        Returns:
            Partial update.
        """
        return synthesize_node(ctx, state)

    return {
        "classify_intent": classify_intent,
        "run_hybrid_search": run_hybrid_search,
        "rerank_results": rerank_results,
        "threshold_gate": threshold_gate,
        "run_text2sql": run_text2sql,
        "synthesize": synthesize,
    }
