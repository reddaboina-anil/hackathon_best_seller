"""LangChain tools and BigQuery execution helpers for the agent."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import structlog
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from lr_bestsellers.exceptions import SQLGenerationError
from lr_bestsellers.models.chunk import SearchResult
from lr_bestsellers.models.query import BqQueryRequest, SqlRow
from lr_bestsellers.store.protocols import (
    COLLECTION_DOMAIN_KNOWLEDGE,
    COLLECTION_GLOSSARY,
    HybridSearchRequest,
    VectorStoreProtocol,
)
from lr_bestsellers.utils.embeddings import EmbedderProtocol

log = structlog.get_logger(__name__)


class HybridSearchInput(BaseModel):
    """Arguments for :func:`hybrid_search_tool`.

    Attributes:
        query: Natural-language search string.
        collection: Qdrant collection name.
        top_k: Candidate count before caller-side rerank.
    """

    query: str = Field(..., min_length=1, description="Search query.")
    collection: str = Field(
        COLLECTION_DOMAIN_KNOWLEDGE,
        description="Qdrant collection to search.",
    )
    top_k: int = Field(10, ge=1, le=100, description="Maximum hits.")


class GlossaryLookupInput(BaseModel):
    """Arguments for :func:`glossary_lookup_tool`.

    Attributes:
        term: Glossary term or question.
        top_k: Maximum glossary hits.
    """

    term: str = Field(..., min_length=1, description="Term to look up.")
    top_k: int = Field(3, ge=1, le=20)


class Text2SqlInput(BaseModel):
    """Arguments for :func:`text2sql_exec_tool`.

    Attributes:
        sql: BigQuery Standard SQL to execute (SELECT/WITH only).
    """

    sql: str = Field(..., min_length=6, description="SELECT or WITH statement.")


@runtime_checkable
class BqRunnerProtocol(Protocol):
    """Executes validated SQL against BigQuery."""

    def execute(self, request: BqQueryRequest) -> list[SqlRow]:
        """Run SQL and return scalar rows.

        Args:
            request: SQL payload.

        Returns:
            Result rows.

        Raises:
            SQLGenerationError: When BigQuery rejects the job.
        """
        ...


class FakeBqRunner:
    """In-memory BQ runner for unit tests."""

    def __init__(self, rows: list[SqlRow] | None = None) -> None:
        """Store canned rows.

        Args:
            rows: Rows returned by every :meth:`execute` call.
        """
        self.rows = rows or [
            SqlRow(
                fields={
                    "dms_segment_id": "99001",
                    "segment_name": "Auto Intenders",
                    "cookie_reach": 1_000_000,
                    "reach_rank": 1,
                }
            )
        ]
        self.last_sql: str | None = None

    def execute(self, request: BqQueryRequest) -> list[SqlRow]:
        """Return canned rows and record SQL.

        Args:
            request: SQL payload.

        Returns:
            Canned ``SqlRow`` list.
        """
        self.last_sql = request.sql
        return list(self.rows)


@runtime_checkable
class BqClientProtocol(Protocol):
    """Subset of ``bigquery.Client`` used at runtime."""

    def query(self, query: str, job_config: object | None = None) -> object:
        """Submit a query job.

        Args:
            query: SQL.
            job_config: Optional job config.

        Returns:
            Query job with ``result()``.
        """
        ...


class BigQueryRunner:
    """Live BigQuery job runner.

    Args:
        client: google.cloud.bigquery.Client
    """

    def __init__(self, client: BqClientProtocol) -> None:
        """Inject a BigQuery client.

        Args:
            client: ``bigquery.Client`` instance.
        """
        self._client = client

    def execute(self, request: BqQueryRequest) -> list[SqlRow]:
        """Execute SQL and coerce rows to ``SqlRow``.

        Args:
            request: SQL payload.

        Returns:
            Scalar rows.

        Raises:
            SQLGenerationError: When the job fails.
        """
        log.info("bq.execute", sql=request.sql)
        try:
            query_job = self._client.query(request.sql)
            raw_rows = list(getattr(query_job, "result")())
        except Exception as exc:
            log.error("bq.execute_failed", error=str(exc), sql=request.sql)
            raise SQLGenerationError("BigQuery execution failed") from exc
        results: list[SqlRow] = []
        for raw in raw_rows:
            mapping = dict(raw)
            fields: dict[str, str | int | float | bool | None] = {}
            for key, value in mapping.items():
                if value is None or isinstance(value, (str, int, float, bool)):
                    fields[str(key)] = value
                else:
                    fields[str(key)] = str(value)
            results.append(SqlRow(fields=fields))
        return results


def run_hybrid_search(
    store: VectorStoreProtocol,
    embedder: EmbedderProtocol,
    payload: HybridSearchInput,
) -> list[SearchResult]:
    """Embed ``payload.query`` and run hybrid search.

    Args:
        store: Vector store.
        embedder: Query embedder.
        payload: Collection and top_k.

    Returns:
        Search hits.
    """
    vector = embedder.embed_query(payload.query)
    return list(
        store.hybrid_search(
            HybridSearchRequest(
                collection=payload.collection,
                query_text=payload.query,
                dense_vector=vector,
                top_k=payload.top_k,
            )
        )
    )


def format_search_results(results: list[SearchResult]) -> str:
    """Render search hits as a plain-text evidence block.

    Args:
        results: Hits to render.

    Returns:
        Multi-line string for LLM context.
    """
    if not results:
        return "(no hits)"
    lines: list[str] = []
    for item in results:
        source = item.chunk.filename
        body = item.parent_text or item.chunk.text
        lines.append(f"[{source} score={item.score:.3f}]\n{body}")
    return "\n\n".join(lines)


def make_hybrid_search_tool(
    store: VectorStoreProtocol,
    embedder: EmbedderProtocol,
) -> StructuredTool:
    """Build a LangChain tool that runs hybrid search.

    Args:
        store: Vector store.
        embedder: Query embedder.

    Returns:
        Structured tool named ``hybrid_search``.
    """

    def _run(query: str, collection: str, top_k: int) -> str:
        """Execute hybrid search and return formatted hits.

        Args:
            query: User query.
            collection: Collection name.
            top_k: Hit cap.

        Returns:
            Formatted evidence string.
        """
        hits = run_hybrid_search(
            store,
            embedder,
            HybridSearchInput(query=query, collection=collection, top_k=top_k),
        )
        return format_search_results(hits)

    return StructuredTool.from_function(
        func=_run,
        name="hybrid_search",
        description="Hybrid BM25 + dense search over a Qdrant collection.",
        args_schema=HybridSearchInput,
    )


def make_glossary_lookup_tool(
    store: VectorStoreProtocol,
    embedder: EmbedderProtocol,
) -> StructuredTool:
    """Build a LangChain tool that searches the glossary collection.

    Args:
        store: Vector store.
        embedder: Query embedder.

    Returns:
        Structured tool named ``glossary_lookup``.
    """

    def _run(term: str, top_k: int) -> str:
        """Look up a glossary term.

        Args:
            term: Term or question.
            top_k: Hit cap.

        Returns:
            Formatted glossary hits.
        """
        hits = run_hybrid_search(
            store,
            embedder,
            HybridSearchInput(query=term, collection=COLLECTION_GLOSSARY, top_k=top_k),
        )
        return format_search_results(hits)

    return StructuredTool.from_function(
        func=_run,
        name="glossary_lookup",
        description="Look up an authoritative domain glossary definition.",
        args_schema=GlossaryLookupInput,
    )


def make_text2sql_exec_tool(runner: BqRunnerProtocol) -> StructuredTool:
    """Build a LangChain tool that executes SELECT SQL.

    Args:
        runner: BigQuery runner.

    Returns:
        Structured tool named ``text2sql_exec``.
    """

    def _run(sql: str) -> str:
        """Execute SQL and return a compact row dump.

        Args:
            sql: SELECT/WITH statement.

        Returns:
            Newline-joined row dumps.
        """
        rows = runner.execute(BqQueryRequest(sql=sql))
        if not rows:
            return "(no rows)"
        return "\n".join(str(row.fields) for row in rows)

    return StructuredTool.from_function(
        func=_run,
        name="text2sql_exec",
        description="Execute read-only BigQuery SQL and return rows.",
        args_schema=Text2SqlInput,
    )
