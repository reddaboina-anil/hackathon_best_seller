# AGENTS.md — lr-bestsellers AI Agent Coding Guide

> **Status**: Complete through Milestone 7. Keep this file in lockstep with the
> tree under `lr_bestsellers/`.

---

## 1. Project Overview

**lr-bestsellers** is a hybrid Agentic RAG + Text2SQL system for querying
LiveRamp syndicated segments. Users ask plain-English questions and receive
grounded, cited answers backed by Qdrant vector search and live BigQuery
queries.

A FastAPI layer exposes one endpoint, `GET /v1/segments`, that either answers a
question (`query` present) or pages an offline CSV dump of the BigQuery features
table (`query` absent).

**Tech stack**: Python 3.13 · uv · LangGraph · Gemini 2.0 Flash ·
gemini-embedding-2 · Qdrant · BigQuery · FastAPI · pydantic v2 · structlog

**Billing vs data**: `Settings.bigquery_project` (`bq_project`) is the GCP
**billing** project. Data tables are fully qualified in `best_sellers.sql`.

---

## 2. Non-Negotiable Invariants

These rules apply to **every file** without exception.

### 2.1 Python & type safety

- `from __future__ import annotations` at the top of **every** `.py` file.
- Full type annotations on every function and method signature.
- `mypy --strict` must pass with zero errors.
- Use `Final` for module-level constants; `Literal` instead of bare string enums.
- Use `typing.Protocol` for interfaces — never `ABC`.

### 2.2 Pydantic v2 at every boundary

- All inter-module data passed as Pydantic `BaseModel` instances — no bare `dict`
  as a public contract (SQL row *values* live on `SqlRow.fields`).
- `Settings(BaseSettings)` via `get_settings()` is the only process singleton
  besides `hooks.metrics.get_metrics()`.
- All other dependencies injected via `__init__` parameters.

### 2.3 Logging — structlog only

```python
import structlog
log = structlog.get_logger(__name__)

log.info("node.start", node="classify_intent", query=query[:50])
log.error("node.error", node="hybrid_search", error=str(exc))
```

Forbidden: `print(...)`, `logging.info(...)` from application code.

CLI answers use `sys.stdout.write` in `main.py`, not `print`.

### 2.4 Error handling

Catch specific failures, log structured fields, re-raise as the most specific
`BestSellersError` subclass with `from exc`. Never `except Exception: pass`.

### 2.5 Google docstrings on every public symbol

Every public function, class, and method uses Google-style docstrings
(Args / Returns / Raises / Example as needed).

### 2.6 LangGraph state is immutable

- `AgentState` is a `TypedDict`; node functions return **partial dicts**.
- I/O goes through injected repositories (`VectorStoreProtocol`, `BqRunnerProtocol`).

---

## 3. Codebase map

```
lr_bestsellers/
├── main.py                          # query(text) + CLI (delegates to service.py)
├── docker-compose.yml               # local Qdrant
├── best_sellers.sql                 # live catalog + metrics SQL
├── csv_dump/*.csv                   # offline BigQuery dump served in browse mode
├── knowledge_base/*.md              # domain docs + glossary
├── lr_bestsellers/
│   ├── __init__.py                  # query(QueryRequest), ingest()
│   ├── __main__.py                  # `python -m lr_bestsellers refresh`
│   ├── config.py                    # Settings + get_settings()
│   ├── exceptions.py                # BestSellersError hierarchy
│   ├── service.py                   # guarded query flow shared by CLI + API
│   ├── api/app.py                   # create_app(), lifespan, exception handlers
│   ├── api/routes.py                # GET /v1/segments (ask + browse branches)
│   ├── api/dependencies.py          # cached Settings + catalog repo providers
│   ├── models/query.py              # QueryRequest/Response, SqlRow, citations
│   ├── models/segment.py            # SegmentDocument
│   ├── models/chunk.py              # ChildChunk, ParentChunk, SearchResult
│   ├── models/catalog.py            # SegmentFeatureRow, CatalogPage, AgentAnswer
│   ├── store/protocols.py           # VectorStoreProtocol, CatalogRepositoryProtocol
│   ├── store/qdrant.py              # QdrantRepository + RRF helpers
│   ├── store/csv_catalog.py         # CsvCatalogRepository (paginated CSV reads)
│   ├── store/sparse.py              # deterministic sparse encoder
│   ├── ingestion/protocols.py       # IngestionSourceProtocol, embed_and_upsert
│   ├── ingestion/file_ingestion.py  # domain_knowledge from markdown
│   ├── ingestion/bq_fetcher.py      # segment_catalog from BigQuery
│   ├── ingestion/glossary_builder.py
│   ├── agent/prompts.py             # Final[str] prompts
│   ├── agent/tools.py               # LangChain StructuredTools + BQ runner
│   ├── agent/nodes.py               # node functions + NodeContext
│   ├── agent/graph.py               # StateGraph, run_query, callbacks
│   ├── guardrails/base.py           # GuardrailChain
│   ├── guardrails/input.py
│   ├── guardrails/sql.py
│   ├── guardrails/output.py
│   ├── hooks/callbacks.py           # SegmentIntelligenceCallbackHandler
│   ├── hooks/metrics.py             # counters + alert rules
│   └── utils/logging.py, chunking.py, reranker.py, embeddings.py
└── tests/unit|integration|evals/
```

---

## 4. Development commands

```bash
uv sync
uv run pytest tests/unit/ -v
uv run ruff check lr_bestsellers tests/
uv run ruff format lr_bestsellers tests/
uv run mypy lr_bestsellers/
uv run python -m lr_bestsellers refresh
uv run python tests/evals/run_evals.py --report
uv run python main.py "What are the top segments by cookie reach?"
uv run uvicorn lr_bestsellers.api.app:app --reload
```

---

## 5. How-to guides

### 5.1 Add a new LangGraph tool

1. Add a Pydantic args model in `lr_bestsellers/agent/tools.py`.
2. Factory-build a `StructuredTool` (inject `VectorStoreProtocol` / `BqRunnerProtocol`).
3. Call the underlying helper from a node in `nodes.py` if the graph should use it
   unconditionally; otherwise bind the tool on the LLM.
4. Add schema tests in `tests/unit/test_tools.py`.
5. Document the tool in `README.md` if operators will see it.

### 5.2 Add a new ingestion source

1. Implement `IngestionSourceProtocol`: `name`, `collection`, `load() -> list[RawDocument]`.
2. Register it in `build_sources()` in `__main__.py` and extend `--source` choices.
3. Map documents through `embed_and_upsert`.
4. Cover `load()` with tmp-path tests in `tests/unit/test_ingestion.py`.

### 5.3 Add a new guardrail

1. Implement `name` + `check(value: str) -> GuardrailResult` (Protocol in `guardrails/base.py`).
2. Insert it into `build_input_chain`, `build_sql_chain`, or `apply_output_guardrails`
   in `service.py`.
3. Raise `InputGuardrailError` / `SQLGuardrailError` / `OutputGuardrailError` via `GuardrailChain`.
4. Add pass and fail cases in `tests/unit/test_guardrails.py`.

### 5.4 Add a glossary term

1. Add an H2 heading and definition in `knowledge_base/glossary.md`.
2. Run `uv run python -m lr_bestsellers refresh --source glossary`.
3. Optional: add a conceptual golden query in `tests/evals/datasets/golden_queries.jsonl`
   (or regenerate via `tests/evals/_generate_datasets.py` and edit).

### 5.5 Add an API endpoint

1. Add the route to `lr_bestsellers/api/routes.py` on the existing `/v1` router,
   with `response_model`, `summary`, and `responses` so OpenAPI stays accurate.
2. Inject dependencies via `Depends` on providers in `api/dependencies.py` —
   never call `get_settings()` inside a handler.
3. Return Pydantic models from `models/`; raise `BestSellersError` subclasses and
   let the handlers in `api/app.py` map them to status codes.
4. Add request and error cases in `tests/unit/test_api.py`, and assert the schema
   change in `TestOpenApiSchema`.

---

## 6. Exception hierarchy

```
BestSellersError
├── RetrievalError
├── EmbeddingError
├── SQLGenerationError
├── IngestionError
├── CatalogError
├── ThresholdNotMetError
└── GuardrailError
    ├── InputGuardrailError
    ├── SQLGuardrailError
    └── OutputGuardrailError
```

HTTP mapping in `api/app.py`: `InputGuardrailError` → 400, other `GuardrailError`
→ 502, `CatalogError` → 503, any other `BestSellersError` → 500.

Always raise the **most specific** subclass and chain with `from exc`.

---

*When adding modules, update section 3 (codebase map) in the same change.*
