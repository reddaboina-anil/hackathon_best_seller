# lr-bestsellers

LiveRamp syndicated segments are easier to *ask about* than to *query*. Reach lives in BigQuery, activation rules live in docs, and glossary terms like cookie_reach are easy to mix up with platform-matched keys.

**lr-bestsellers** is an agent that sits in the middle: you send a plain-English question to the API, it decides whether to search the knowledge base, run live SQL, or both, and it returns structured segment objects — not markdown prose.

You do **not** need to know LangGraph to use it. Operators paste Confluence notes into `knowledge_base/`, run a refresh command, and ask questions like “what are the top segments by cookie reach?”

---

## Why this is not “just RAG”

Analytics questions (“top by reach on TTD”) need **live numbers**, not a paragraph from a wiki. Conceptual questions (“what is FULL delivery?”) need **docs**. Vague questions need **both**, plus a willingness to say “I don’t know” when similarity is too low.

The orchestrator is **LangGraph**. Retrieval is **Qdrant** (dense + sparse hybrid search). Metrics stay in **BigQuery**; the vector catalog stores names and descriptions only.

---

## High-level architecture

```mermaid
flowchart TD
    subgraph sources [Data Sources]
        KB["knowledge_base/\n*.md files"]
        BQ["BigQuery\nbest_sellers.sql"]
        Gloss["glossary.md"]
    end

    subgraph store [Knowledge Store]
        Qdrant["Qdrant\n3 collections\nhybrid BM25 + dense"]
        BigQueryLive["BigQuery live SQL"]
    end

    subgraph agent [Intelligence]
        Graph["LangGraph + Gemini 2.0 Flash"]
    end

    subgraph safety [Safety]
        Guards["Input / SQL / Output guardrails"]
        Hooks["Callbacks + metrics"]
    end

    subgraph output [Response]
        Answer["SegmentQueryResponse\nsegments[] + narrative + citations"]
    end

    KB -->|"refresh CLI"| Qdrant
    BQ -->|"refresh CLI"| Qdrant
    Gloss -->|"refresh CLI"| Qdrant
    Qdrant --> Graph
    BigQueryLive --> Graph
    Graph --> Guards
    Guards --> Answer
    Graph --> Hooks
```



---

## Ingestion layer — how it works

### The big picture

Think of ingestion as **teaching the system before you can ask it questions**.

The system cannot answer questions about segments, glossary terms, or activation docs out of thin air. Before it can help, it needs to:

1. **Read** all your content (docs, glossary, segment names).
2. **Convert each piece of text into a numeric fingerprint** — called an *embedding* — that captures its meaning, not just its words. Two sentences that mean the same thing get similar fingerprints even if they use different words.
3. **Store those fingerprints in a fast search index** (Qdrant), so that when a user asks a question, the system can instantly find the most relevant pieces.

You run ingestion once (or whenever your content changes). Asking questions never touches this step.

---

### What gets ingested and where it goes

There are three separate "libraries" (called *collections*) stored in Qdrant:


| Library            | What goes in                                         | Comes from                   |
| ------------------ | ---------------------------------------------------- | ---------------------------- |
| `domain_knowledge` | How-to docs, activation guides, platform notes       | `knowledge_base/*.md` files  |
| `glossary`         | Term definitions (cookie_reach, FULL delivery, etc.) | `knowledge_base/glossary.md` |
| `segment_catalog`  | Segment names + descriptions (no live numbers)       | BigQuery or local CSV        |


Live metrics (reach, buyers, distribution) are **never stored** in Qdrant. They are fetched fresh from BigQuery every time someone asks an analytics question.

---

### Source 1 — Knowledge base docs (`knowledge_base/*.md`)

Plain-English documentation lives as markdown files. Here is what happens when you refresh:

```mermaid
flowchart TD
    A["knowledge_base/activation.md"] --> B["Step 1 — Split by H2 headings\nEach ## section = one Parent chunk\n(stored in full as answer context)"]
    B --> C["Step 2 — Split each parent into\n~300-token Child chunks\n(smaller = more precise search)"]
    C --> D["Step 3 — Embed each child\nGemini API: text → 768 numbers\n(numeric fingerprint of meaning)"]
    D --> E[("Qdrant: domain_knowledge\nChild stored with parent text attached\nRetrieval uses child · Answer uses parent")]
```

**Why split into parent and child?** Smaller chunks find better matches (precision). But the LLM needs more context to write a good answer, so the parent text is always attached. Best of both worlds.

---

### Source 2 — Glossary (`glossary.md`)

Each term under a `##` heading becomes one entry. No splitting — a definition is already small enough.

```mermaid
flowchart TD
    A["glossary.md\n## cookie_reach\nNumber of LiveRamp cookies matched to a segment."]
    B["One embedding per ## term\nGemini API: definition text → 768 numbers"]
    C[("Qdrant: glossary\none point per term")]
    A --> B --> C
```

---

### Source 3 — Segment catalog (the large one)

The segment catalog has **over 1 million rows**. Processing all of them at once would crash your laptop and exhaust the Gemini API. So the system processes them in small, manageable batches. There are two ways to load the catalog:

#### Path A — Direct BigQuery query (`--source bq`)

The system queries BigQuery live and pages through results 1,000 rows at a time.

```mermaid
flowchart TD
    BQ[("BigQuery\n1.1 million segments")]
    P1["Page 1 — rows 0–999\nCATALOG_PAGE_SIZE = 1000\none BigQuery job per page"]
    P2["Page 2 — rows 1000–1999\none BigQuery job"]
    PN["… repeat ~1,100 times\nuntil no rows remain"]

    B1["Embed Batch 1\n100 texts → Gemini API"]
    B2["Embed Batch 2\n100 texts → Gemini API"]
    B10["Embed Batch 10\n100 texts → Gemini API"]
    note["EMBED_BATCH_SIZE = 100\n10 Gemini calls per page"]

    Q[("Qdrant: segment_catalog\nupsert 1,000 vectors per page")]

    BQ --> P1
    P1 -->|"split into 10 batches"| B1
    P1 --> B2
    P1 --> B10
    B1 & B2 & B10 --> note --> Q
    Q --> P2 --> PN
```

**`CATALOG_PAGE_SIZE = 1000`** — how many rows come back from BigQuery per query.
**`EMBED_BATCH_SIZE = 100`** — how many texts are sent to Gemini per API call.

These two work at different levels and are independent. You can change them separately.

#### Path B — Local CSV export (`--source csv`) ← recommended

If you have already downloaded the catalog as a CSV file, this path skips BigQuery entirely. It reads the file from disk in 100-row pages.

```mermaid
flowchart TD
    CSV["dms_segmets_best_sellers.csv\nlocal file — no BigQuery needed"]
    R1["Read rows 1–100 from disk\nEMBED_BATCH_SIZE = 100"]
    R2["Read rows 101–200 from disk"]
    RN["… repeat until end of file"]

    E1["Embed batch → Gemini API\n100 texts → 100 vectors"]
    E2["Embed batch → Gemini API\n100 texts → 100 vectors"]

    Q[("Qdrant: segment_catalog\nupsert after every batch")]

    CSV --> R1 --> E1 --> Q
    Q --> R2 --> E2 --> RN
```

**Why is CSV faster?** No network round-trip to BigQuery per page. Reading from disk is nearly instant, so the only bottleneck is the Gemini API (which is the same for both paths). Fewer moving parts = fewer things that can fail.

#### Choosing between BQ and CSV


|                       | `--source bq`           | `--source csv`                        |
| --------------------- | ----------------------- | ------------------------------------- |
| Needs BigQuery access | Yes                     | No (just the file)                    |
| Always up to date     | Yes                     | Only as fresh as the export           |
| Speed                 | Slower (~1,100 BQ jobs) | Faster (disk reads)                   |
| Network dependency    | BigQuery + Gemini       | Gemini only                           |
| Good for              | CI / automated refresh  | Developer setup, large one-time loads |


---

### How "upsert" keeps re-runs safe

Every ingestion uses **upsert** (update-or-insert), keyed on `dms_segment_id`. If a segment was already stored, its record is overwritten with the new embedding. No duplicates are created. This means:

- You can safely re-run ingestion at any time.
- You can stop a run halfway and resume — whatever already landed stays valid.
- Switching from BQ to CSV (or vice versa) overwrites matching IDs cleanly.

---

### Full ingestion pipeline — all sources

```mermaid
flowchart TD
    subgraph input [" Your content "]
        Files["📄 knowledge_base/*.md\nDocs and guides"]
        Gloss["📖 glossary.md\nTerm definitions"]
        BQTable["🗄️ BigQuery table\n1.1M segment rows"]
        CSV["📁 Local CSV export\ndms_segmets_best_sellers.csv"]
    end

    subgraph process [" Processing "]
        Chunk["Split into H2 parent sections\nthen ~300-token child chunks"]
        EmbedDocs["Gemini Embedding API\n100 texts per call\ntext → 768 numbers"]
        EmbedGloss["Gemini Embedding API\none call per term"]
        BQPage["BigQuery job\nLIMIT 1000 OFFSET n\n~1,100 jobs total"]
        CSVPage["Read from disk\n100 rows at a time\nno BigQuery needed"]
        EmbedCat["Gemini Embedding API\n100 texts per call\ntext → 768 numbers"]
    end

    subgraph store [" Qdrant search index "]
        DK[("domain_knowledge\nchild chunks + parent context")]
        GL[("glossary\none point per term")]
        SC[("segment_catalog\nnames + descriptions only\nlive metrics stay in BigQuery")]
    end

    Files --> Chunk --> EmbedDocs --> DK
    Gloss --> EmbedGloss --> GL
    BQTable -->|"--source bq"| BQPage --> EmbedCat --> SC
    CSV -->|"--source csv"| CSVPage --> EmbedCat
```



---

### Refresh commands

```bash
# Refresh everything (docs + glossary + BQ catalog)
uv run python -m lr_bestsellers refresh

# Refresh one source at a time
uv run python -m lr_bestsellers refresh --source files
uv run python -m lr_bestsellers refresh --source glossary
uv run python -m lr_bestsellers refresh --source bq          # live BigQuery query (slower)
uv run python -m lr_bestsellers refresh --source csv         # local export (faster, see below)

# Seed canonical platform names for the platform resolver (run after BQ data changes)
uv run python -m lr_bestsellers refresh --source platform_names

# Refresh a single doc file
uv run python -m lr_bestsellers refresh --file knowledge_base/activation.md

# Wipe all collections first, then re-ingest everything
uv run python -m lr_bestsellers refresh --reset

# Resume a CSV ingestion that was interrupted at row N (skip already-embedded rows)
uv run python -m lr_bestsellers refresh --source csv --skip-rows 479000
```

> **Platform resolver**: the `platform_names` source seeds a small Qdrant collection with every
> canonical platform name from `active_platform_names` (e.g. `The Trade Desk`, `Google DV360`).
> Run it once after initial setup and again whenever the live platform list changes.
> Without it the resolver degrades gracefully — platform queries still work via
> `REGEXP_REPLACE` normalisation, with a zero-row retry loop as a fallback.

`BQ_PROJECT` / `BIGQUERY_PROJECT` is the **billing** project (for example `liveramp-eng-qa-reliability`). Table names are fully qualified inside `best_sellers.sql`. If `GOOGLE_APPLICATION_CREDENTIALS` is set, that service-account file is used; otherwise Application Default Credentials (ADC).

---

## Retrieval and anti-hallucination

```mermaid
flowchart TD
    Q["User query"] --> Embed["gemini-embedding-2"]
    Embed --> BM25["Sparse BM25-style search"]
    Embed --> Dense["Dense cosine search"]
    BM25 --> RRF["Reciprocal Rank Fusion"]
    Dense --> RRF
    RRF --> Rerank["CrossEncoderReranker\ntop-k_final"]
    Rerank --> Gate{"score ≥ similarity_threshold?"}
    Gate -->|No, and no SQL| Fallback["Grounded fallback"]
    Gate -->|Yes| LLM["Gemini synthesize + citations"]
    LLM --> Out["QueryResponse"]
    Fallback --> Out
```



---

## LangGraph state machine

```mermaid
stateDiagram-v2
    [*] --> classify_intent
    classify_intent --> vector_path: conceptual / lookup
    classify_intent --> sql_path: analytics
    classify_intent --> both_paths: mixed / vague

    state vector_path {
        run_hybrid_search --> rerank_results
        rerank_results --> threshold_gate
    }
    state sql_path {
        run_text2sql
    }
    state both_paths {
        vector_then_sql: hybrid then SQL
    }

    vector_path --> synthesize
    sql_path --> synthesize
    both_paths --> synthesize
    synthesize --> [*]
```



---

## Storage layer

```mermaid
flowchart TB
    subgraph collections [Qdrant collections]
        SC["segment_catalog\nSegmentDocument text"]
        DK["domain_knowledge\nchild chunks + parent_text payload"]
        GL["glossary\none point per term"]
    end
    Client["QdrantRepository\nVectorStoreProtocol"] --> collections
    Fake["FakeVectorStore\nunit tests"] -.-> Client
```



---

## Local setup

**Prerequisites:** Docker Desktop, Python **3.13**, [uv](https://docs.astral.sh/uv/), a Gemini API key, and (for live SQL) GCP access to the BigQuery **billing** project.

### 1. Clone and install

```bash
git clone <repo-url>
cd hackathon_best_seller
uv sync
```

### 2. Start Qdrant locally

`docker-compose.yml` runs Qdrant `v1.13.4` with REST on port `6333` and gRPC on `6334`. Vectors persist in the Docker volume `qdrant_storage`.

```bash
docker compose up -d
docker compose ps
curl -s http://localhost:6333/readyz
```

A healthy instance returns HTTP 200. The dashboard is at [http://localhost:6333/dashboard](http://localhost:6333/dashboard).

Leave `QDRANT_URL=http://localhost:6333` and do **not** set `QDRANT_API_KEY` for local Docker. For Qdrant Cloud, set both the cluster URL and API key.

```bash
# Inspect collections after ingest
curl -s http://localhost:6333/collections | python -m json.tool

# Stop Qdrant (keep stored vectors)
docker compose down

# Stop and delete the volume (wipe local collections)
docker compose down -v
```

### 3. Create `.env` and fill credentials

```bash
cp .env.example .env
```

Edit `.env` — never commit it or a service-account JSON.


| What you need                | Variable                         | How to get it                                                                                                                                            |
| ---------------------------- | -------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Gemini + embeddings          | `GOOGLE_API_KEY`                 | [Google AI Studio](https://aistudio.google.com/app/apikey). Alias: `GEMINI_API_KEY`.                                                                     |
| BigQuery **billing** project | `BIGQUERY_PROJECT`               | GCP project that pays for jobs (for example `liveramp-eng-qa-reliability`). Alias: `BQ_PROJECT`. Data tables stay fully qualified in `best_sellers.sql`. |
| BigQuery auth (local)        | `GOOGLE_APPLICATION_CREDENTIALS` | Absolute path to a service-account JSON. Uncomment the line in `.env`. On GKE / Cloud Run, leave unset and use ADC.                                      |
| Local Qdrant                 | `QDRANT_URL`                     | Default `http://localhost:6333`.                                                                                                                         |
| Qdrant Cloud only            | `QDRANT_API_KEY`                 | Leave blank for local Docker.                                                                                                                            |


Settings load from `.env` at process start via `get_settings()`. After you change keys or the credentials path, restart the CLI / process. Re-run `refresh` if you need new embeddings after rotating `GOOGLE_API_KEY`.

**Example `.env` (local laptop):**

```bash
GOOGLE_API_KEY=AIzaSy...your-studio-key

BIGQUERY_PROJECT=liveramp-eng-qa-reliability
GOOGLE_APPLICATION_CREDENTIALS=/Users/yourname/.gcp/lr-bestsellers-sa.json

QDRANT_URL=http://localhost:6333
# QDRANT_API_KEY=   # leave unset locally

ENVIRONMENT=development
LOG_LEVEL=INFO
```

### 4. Authenticate to GCP (if you are not using a service-account file)

```bash
gcloud auth application-default login
gcloud config set project liveramp-eng-qa-reliability
```

### 5. Ingest into Qdrant

```bash
uv run python -m lr_bestsellers refresh
```

This loads `knowledge_base/*.md` into `domain_knowledge`, `glossary.md` into `glossary`, and `best_sellers.sql` into `segment_catalog` (names and descriptions only; live metrics stay in BigQuery). A placeholder `GOOGLE_API_KEY` uses a hash embedder so you can still stand up the collections.

Useful variants:

```bash
uv run python -m lr_bestsellers refresh --source glossary
uv run python -m lr_bestsellers refresh --source bq                # live BigQuery query (slower)
uv run python -m lr_bestsellers refresh --source csv               # local export (faster, see below)
uv run python -m lr_bestsellers refresh --source platform_names    # seed platform name resolver
uv run python -m lr_bestsellers refresh --file knowledge_base/activation.md
uv run python -m lr_bestsellers refresh --reset   # wipe collections, then re-ingest
```

> **`--source platform_names`** must be run at least once after initial setup (and again after
> BigQuery platform data changes) to enable the platform name resolver. It reads every distinct
> value from `active_platform_names` in BigQuery and stores them in a sparse Qdrant collection
> so queries like `"activated to tradedesk"` can be resolved to `"The Trade Desk"` before the
> SQL prompt is sent to Gemini.

#### Using the CSV catalog export (recommended for large catalogs)

BigQuery exports can be slow and expensive. An alternative is to download the segment
catalog as a CSV (BigQuery UI → **Export → Download as CSV**) and use `--source csv`:

```bash
# Place the export in the repo root with this exact name…
dms_segmets_best_sellers.csv

# …then run:
uv run python -m lr_bestsellers refresh --source csv

# Or point to any path:
uv run python -m lr_bestsellers refresh --source csv --file /path/to/export.csv
```

Required CSV columns (case-insensitive, UTF-8 BOM OK):
`dms_segment_id`, `seller_customer_id`, `segment_name`, `segment_description`.

The CSV file is **git-ignored** — do not commit it.

#### Resuming an interrupted CSV ingestion

If a large CSV run is interrupted (network timeout, DNS failure, Ctrl-C), Qdrant already
holds everything that was upserted before the crash. Because every upsert is keyed on
`dms_segment_id`, restarting from scratch is safe but wastes hours re-embedding rows that
are already indexed.

Use `--skip-rows N` to jump past the rows already processed and continue from where the
run stopped:

```bash
# Interrupted after ~479,000 rows? Resume from row 479,000:
uv run python -m lr_bestsellers refresh --source csv --skip-rows 479000
```

`--skip-rows` counts **data rows** (header excluded). The value does not need to be exact —
a small overlap (a few hundred rows) is harmless because Qdrant upserts overwrite existing
points by ID. Pick the last round number logged before the crash (`ingest.page_upserted`
log lines report the cumulative `total` count).

### 6. Ask a question

```bash
uv run python main.py "What are the top segments by cookie reach?"
```

Conceptual check after glossary ingest: `uv run python main.py "What is cookie_reach?"` — expect a cited answer with `[Source: …]`.

### 7. Serve the HTTP API

```bash
uv run uvicorn lr_bestsellers.api.app:app --reload
```

Swagger UI is at [http://localhost:8000/docs](http://localhost:8000/docs), ReDoc at `/redoc`, and the OpenAPI schema at `/openapi.json`.

---

## HTTP API

### `POST /v1/query` — Structured segment search *(primary endpoint)*

Submit a plain-English question and receive each matched segment as a typed object. No markdown to parse.

**Request**

```bash
curl -X POST http://localhost:8000/v1/query \
  -H "Content-Type: application/json" \
  -d '{"query": "Frequent international travelers, business class preferred"}'
```

Request body fields:

| Field | Type | Default | Description |
|---|---|---|---|
| `query` | string | required | Plain-English question, 1–2000 characters. |
| `caller_id` | string | `"api"` | Opaque rate-limit bucket key. |

**Response**

```json
{
  "query": "Frequent international travelers, business class preferred",
  "intent": "mixed",
  "confidence": 0.91,
  "total_found": 6,
  "segments": [
    {
      "rank": 1,
      "dms_segment_id": "123456",
      "segment_name": "AlikeAudience: United States > Interest > Luxury Travel > First Class/Business Class Flights",
      "description": "Includes people residing in the United States who are interested in first class/business class flights based on mobile app downloads, usage, and luxury travel behavior.",
      "distribution_rank": 14090,
      "impressions_rank": null,
      "provider_revenue_rank": null,
      "buyer_usage_rank": null,
      "platform_usage_rank": null,
      "active_platform_names": ["The Trade Desk", "Google DV360"],
      "source": "BigQuery",
      "relevance_score": null
    },
    {
      "rank": 2,
      "dms_segment_id": "789012",
      "segment_name": "Experian > Lifestyle and Interests (Affinity) > Travel > Frequent International Travelers",
      "description": "Consumers who regularly travel abroad for leisure or business.",
      "distribution_rank": 10283,
      "impressions_rank": null,
      "provider_revenue_rank": null,
      "buyer_usage_rank": null,
      "platform_usage_rank": null,
      "active_platform_names": [],
      "source": "BigQuery",
      "relevance_score": null
    }
  ],
  "narrative": "Here are the relevant LiveRamp syndicated segments...",
  "sql_used": "WITH bestsellers AS (...) SELECT segment_name, dms_segment_id, distribution_rank FROM ...",
  "citations": [
    { "source": "BigQuery", "text": "...", "score": 1.0 }
  ],
  "processing_time_ms": 2341
}
```

Response field reference:

| Field | Type | Description |
|---|---|---|
| `query` | string | The original question. |
| `intent` | string | Classified routing intent: `analytics`, `conceptual`, `lookup`, `mixed`, or `vague`. |
| `confidence` | float | Agent self-assessed confidence, 0–1. |
| `total_found` | int | Number of distinct segments returned. |
| `segments` | array | Ordered list of matched segment objects (see below). |
| `narrative` | string | LLM-generated prose answer with inline citations — useful for human-facing UIs. |
| `sql_used` | string \| null | BigQuery SQL executed, or null when only vector search was used. |
| `citations` | array | Evidence fragments cited in the answer. |
| `processing_time_ms` | int \| null | Wall-clock milliseconds for the full pipeline call. |

Each `segments[]` object:

| Field | Type | Description |
|---|---|---|
| `rank` | int | 1-based result position. |
| `dms_segment_id` | string \| null | Unique LiveRamp segment identifier. |
| `segment_name` | string | Human-readable segment taxonomy path. |
| `description` | string \| null | Segment description text. |
| `distribution_rank` | int \| null | Dense rank by distribution footprint (1 = widest). |
| `impressions_rank` | int \| null | Dense rank by impressions (1 = highest). |
| `provider_revenue_rank` | int \| null | Dense rank by provider net revenue (1 = highest). |
| `buyer_usage_rank` | int \| null | Dense rank by buyers with usage (1 = highest). |
| `platform_usage_rank` | int \| null | Dense rank by platforms with usage (1 = highest). |
| `active_platform_names` | array of strings | Platforms the segment is currently distributed to. |
| `source` | string | `"BigQuery"`, `"VectorSearch"`, or `"hybrid"`. |
| `relevance_score` | float \| null | Cosine similarity score (vector search only; null for SQL results). |

---

### `GET /v1/health` — Liveness check

```bash
curl http://localhost:8000/v1/health
```

```json
{ "status": "ok", "version": "1.0.0" }
```

No I/O — confirms the process is alive. Use this for load balancer health checks.

---

### `GET /v1/segments` — Ask a question, or browse the catalog

One endpoint, two behaviours, chosen by whether `query` is present. Responses are discriminated by `mode` (`agent` or `catalog`).

```mermaid
flowchart LR
    Req["GET /v1/segments"] --> Branch{"query supplied?"}
    Branch -->|Yes| Guarded["Input guardrails → LangGraph → output guardrails"]
    Guarded --> Agent["AgentAnswer\nmode = agent"]
    Branch -->|No| CSV["CsvCatalogRepository\ncsv_dump/*.csv parsed once, cached"]
    CSV --> Page["CatalogPage\nmode = catalog"]
```

| Parameter | Where | Default | Meaning |
|---|---|---|---|
| `query` | query string | *(none)* | Plain-English question, up to 2000 characters. Blank or absent switches to browse mode. |
| `page` | query string | `1` | 1-based page number. Browse mode only. |
| `page_size` | query string | `50` | Rows per page, max `200`. Browse mode only. |
| `X-Caller-Id` | header | `api` | Rate-limit bucket key. Ask mode only. |

**Browse mode** — no `query`, so no LLM and no BigQuery call. Pages
`csv_dump/best_sellers_output.csv` (the `best_sellers.sql` export). The file is
parsed on first request and cached.

```bash
curl -s "http://localhost:8000/v1/segments?page=1&page_size=2"
```

```json
{
  "mode": "catalog",
  "source": "best_sellers_output.csv",
  "pagination": {
    "page": 1,
    "page_size": 2,
    "total_items": 14633,
    "total_pages": 7317,
    "has_next": true,
    "has_previous": false
  },
  "items": [
    {
      "dms_segment_id": 32003,
      "segment_name": "Acxiom US Demographic > Age > Adult Age in HH > 35-44",
      "segment_description": "Someone in the household is between the ages of 35-44",
      "active_platform_names": ["A&E Networks", "Altice / NYI", "Ampersand"],
      "ios_reach": 1200000,
      "is_highly_distributed": true
    }
  ]
}
```

**Ask mode** — `query` present, so the question runs through the same guarded pipeline the CLI uses.

```bash
curl -s "http://localhost:8000/v1/segments?query=Which%20segments%20earn%20the%20most%20provider%20revenue%3F"
```

```json
{
  "mode": "agent",
  "query": "Which segments earn the most provider revenue?",
  "result": {
    "answer": "… [Source: BigQuery:best_sellers]",
    "sources": [{"source": "BigQuery", "text": "…", "score": 1.0}],
    "sql_used": "SELECT … LIMIT 1000",
    "confidence": 0.91,
    "intent": "analytics"
  }
}
```

---

### Error responses

All errors share one envelope:

```json
{ "error": "<CODE>", "detail": "<human-readable message>" }
```

| Status | When | `error` |
|---|---|---|
| `400` | Input guardrail rejected the query | Guardrail code, e.g. `PII_DETECTED` |
| `422` | Parameter validation failed (`page=0`, `page_size=500`, query over 2000 chars) | FastAPI validation body |
| `500` | Retrieval, embedding, or SQL generation failure | `PIPELINE_ERROR` |
| `502` | Answer failed an output guardrail | Guardrail code, e.g. `MISSING_CITATION` |
| `503` | CSV dump missing or malformed | `CATALOG_UNAVAILABLE` |

---

## Segment recommendation tags (standalone)

A second, **completely separate** service attaches Amazon-style badges to segments
(`High iOS Reach`, `Top Facebook Activated`, `Buyer Magnet`, …). It does **not**
use Gemini, BigQuery, or any secret. Tags are pre-computed from the
`best_sellers.sql` CSV dump, stored in DuckDB, and served on port **8001**.

`lr_bestsellers/` is untouched. The tag API lives in `tag_api/` with its own
`pyproject.toml`.

```mermaid
flowchart LR
    subgraph manual [One-time manual step]
        A["BigQuery UI\nRun best_sellers.sql"]
        B["Download as CSV\ndrop in csv_dump/best_sellers_output.csv"]
    end

    subgraph compute [On-demand]
        C["docker compose --profile compute\nrun --rm tag-compute"]
        D[("duckdb_data/tags.duckdb")]
    end

    subgraph api [Always live]
        E["tag-api :8001\nFastAPI + DuckDB read-only"]
    end

    A --> B --> C --> D --> E
```

### Quick start (tag API)

Zero local Python installs. Zero secrets. Someone with BigQuery access runs
`best_sellers.sql`, exports a CSV, and shares `csv_dump/best_sellers_output.csv`.

```bash
# 1. Drop the CSV export of `best_sellers.sql` in place (header row required).
#    Required columns include cookie_reach, ios_reach, android_reach, active_platforms,
#    active_platform_names, active_buyers, is_highly_distributed.
#    csv_dump/best_sellers_output.csv

# 2. Compute tags (stop tag-api first so DuckDB is not locked)
docker compose stop tag-api
docker compose --profile compute run --rm tag-compute

# 3. Start the tag API (--wait blocks until /healthz succeeds)
docker compose up -d --wait --build --force-recreate tag-api

# 4. Verify
curl http://localhost:8001/healthz
curl http://localhost:8001/v1/tags
curl "http://localhost:8001/v1/segments?page=1&size=10"
curl http://localhost:8001/v1/segments/1015151361/tags
curl "http://localhost:8001/v1/tags/high_ios_reach/segments?page=1&size=10"
```

Swagger UI is at [http://localhost:8001/docs](http://localhost:8001/docs). If
`tags.duckdb` has not been computed yet the API still starts and returns empty
lists (Null Object store).

Re-run step 2 whenever the CSV is refreshed.

### The 11 computable tags

These are the only tags available from `best_sellers.sql`. Revenue / impressions
tags are deferred until those columns exist in the dump.

| Tag slug | Category | Logic |
|---|---|---|
| `top_facebook_activated` | platform | `'Facebook'` in `active_platform_names` **and** `is_highly_distributed` |
| `top_ttd_activated` | platform | `'The Trade Desk'` in `active_platform_names` **and** `is_highly_distributed` |
| `top_google_activated` | platform | `'Google \| Data Marketplace'` in `active_platform_names` **and** `is_highly_distributed` |
| `multi_platform_powerhouse` | platform | `active_platforms >= 5` |
| `high_ios_reach` | reach | `ios_reach >= p90(ios_reach)` |
| `high_android_reach` | reach | `android_reach >= p90(android_reach)` |
| `massive_cookie_scale` | reach | `cookie_reach >= p99(cookie_reach)` |
| `cross_device_champion` | reach | cookie, iOS, and Android reach all `>= p80` |
| `highly_distributed` | distribution | `is_highly_distributed = true` |
| `buyer_magnet` | distribution | `active_buyers >= p90(active_buyers)` |
| `broad_platform_breadth` | distribution | `active_platforms >= 4` |

Adding a tag is a SQL-only change in `compute_tags.sql` (one column in `tagged`,
one VALUES row, one UNPIVOT entry). No Python and no Docker rebuild.

### Tag HTTP API

| Method | Path | Response |
|---|---|---|
| `GET` | `/v1/tags` | All 11 tag definitions, ordered by priority |
| `GET` | `/v1/segments?page=1&size=50` | Paginated dump rows, each with assigned tags |
| `GET` | `/v1/segments/{segment_id}/tags` | Tags assigned to that segment (empty list if none) |
| `GET` | `/v1/tags/{slug}/segments?page=1&size=50` | Paginated segment IDs for a tag |

Errors share the same envelope as the AI API, `{"error": "<CODE>", "detail": "<message>"}`:

| Status | When | `error` |
|---|---|---|
| `404` | Unknown tag slug | `TAG_NOT_FOUND` |
| `422` | `page < 1` or `size > 200` | FastAPI validation body |
| `503` | DuckDB file exists but cannot be read | `TAG_STORE_UNAVAILABLE` |

`tag-compute` and `tag-api` take **no** `.env` and **no** GCP credentials. The
CSV is bind-mounted read-only; `tags.duckdb` lives on the host at
`duckdb_data/tags.duckdb`.

To run the tag API's own checks (from `tag_api/`):

```bash
cd tag_api
uv sync
uv run ruff check .
uv run ruff format --check .
uv run mypy .
uv run pytest tests/ -v --tb=short --cov=. --cov-report=term-missing
```

The SQL rules are also covered from the repo root:

```bash
uv run pytest tests/unit/test_compute_sql.py -v
```

---

## Quick start

If the stack is already familiar:

1. `cp .env.example .env` and set `GOOGLE_API_KEY` plus `BIGQUERY_PROJECT` (and optionally `GOOGLE_APPLICATION_CREDENTIALS`).
2. `docker compose up -d` then `curl -s http://localhost:6333/readyz`
3. `uv sync`
4. `uv run python -m lr_bestsellers refresh`
5. `uv run python main.py "What are the top segments by cookie reach?"`
6. `uv run uvicorn lr_bestsellers.api.app:app --reload` then `curl -X POST http://localhost:8000/v1/query -H "Content-Type: application/json" -d '{"query": "What are the top segments by cookie reach?"}'`

### Troubleshooting: `invalid peer certificate: UnknownIssuer`

On corporate networks with TLS inspection, uv's default rustls stack cannot verify the intercepted PyPI certificate. `pyproject.toml` already sets `[tool.uv] native-tls = true`, which makes uv use the macOS Keychain (where IT typically installs the intercept CA). If the error persists try:

```bash
# Option 1: force native TLS for a single run (already the project default)
UV_NATIVE_TLS=1 uv run python -m lr_bestsellers refresh --source glossary

# Option 2: point uv at a specific PEM bundle (export it from Keychain first)
export SSL_CERT_FILE=/path/to/corp-ca-bundle.pem
export REQUESTS_CA_BUNDLE="$SSL_CERT_FILE"
uv run python -m lr_bestsellers refresh --source glossary

# Option 3: skip package fetch entirely when venv is already complete
uv run --offline python -m lr_bestsellers refresh --source glossary

# Option 4: last resort — allow insecure TLS for PyPI only (do NOT commit this)
uv run --allow-insecure-host pypi.org --allow-insecure-host files.pythonhosted.org \
  python -m lr_bestsellers refresh --source glossary
```

---

## Environment reference


| Variable                         | Settings field                    | Meaning                                   |
| -------------------------------- | --------------------------------- | ----------------------------------------- |
| `GOOGLE_API_KEY`                 | `google_api_key`                  | Gemini + embeddings                       |
| `LLM_MODEL` / `GEMINI_MODEL`     | `llm_model`                       | Chat model (default `gemini-3.6-flash`)   |
| `EMBEDDING_MODEL`                | `embedding_model`                 | Embeddings (default `gemini-embedding-2`) |
| `BIGQUERY_PROJECT`               | `bigquery_project` / `bq_project` | **Billing** project for jobs              |
| `GOOGLE_APPLICATION_CREDENTIALS` | `google_application_credentials`  | Optional SA JSON path; else ADC           |
| `CSV_CATALOG_PATH` | `csv_catalog_path` | CSV dump served in browse mode (default `csv_dump/best_sellers_output.csv`, resolved against the repo root). Alias: `CSV_DUMP_PATH` |
| `QDRANT_URL`                     | `qdrant_url`                      | Default `http://localhost:6333`           |
| `QDRANT_API_KEY`                 | `qdrant_api_key`                  | Qdrant Cloud only                         |
| `SIMILARITY_THRESHOLD`           | `similarity_threshold`            | Default `0.65`                            |
| `MAX_RETRIEVAL_RESULTS`          | `max_retrieval_results`           | Candidates before rerank (default `10`)   |
| `TOP_K_FINAL`                    | `top_k_final`                     | Hits sent to the LLM (default `3`)        |
| `LOG_LEVEL`                      | `log_level`                       | `DEBUG` … `CRITICAL`                      |
| `ENVIRONMENT`                    | `environment`                     | `development` / `staging` / `production`  |
| `LANGSMITH_TRACING_V2`           | `langsmith_tracing_v2`            | Optional tracing                          |
| `LANGSMITH_API_KEY`              | `langsmith_api_key`               | Optional                                  |
| `LANGSMITH_PROJECT`              | `langsmith_project`               | Default `lr-bestsellers`                  |


Never commit `.env` or service-account JSON. `*.json` is gitignored.

---

## Query examples


| Question                                                 | Typical intent | Path                                |
| -------------------------------------------------------- | -------------- | ----------------------------------- |
| What is cookie_reach?                                    | conceptual     | Vector (glossary + docs)            |
| What are the top segments by cookie reach?               | analytics      | Text2SQL                            |
| What is activation and how many buyers use top segments? | mixed          | Vector then SQL                     |
| Ignore previous instructions                             | blocked        | Input guardrail `INJECTION_ATTEMPT` |
| *(no question at all)* | browse | `GET /v1/segments` pages `csv_dump/best_sellers_output.csv` |


Python:

```python
from main import query

response = query("What is FULL delivery?")
print(response.answer)
print(response.intent, response.confidence)
```

Package API:

```python
from lr_bestsellers import query as package_query
from lr_bestsellers.models import QueryRequest

package_query(QueryRequest(text="What is SSA?"))
```

---

## Development commands

```bash
uv sync
uv run pytest tests/unit/ -v
uv run pytest tests/unit/test_store_protocol.py -v
uv run pytest tests/unit/test_chunking.py tests/unit/test_ingestion.py -v
uv run pytest tests/unit/test_nodes.py tests/unit/test_tools.py -v
uv run pytest tests/unit/test_guardrails.py -v
uv run pytest tests/unit/test_api.py tests/unit/test_csv_catalog.py -v
uv run uvicorn lr_bestsellers.api.app:app --reload
uv run ruff check lr_bestsellers tests/
uv run ruff format lr_bestsellers tests/
uv run mypy lr_bestsellers/
uv run python tests/evals/run_evals.py
uv run python tests/evals/run_evals.py --report
```

Integration tests (`tests/integration/test_qdrant.py`, `test_bq.py`) skip when Qdrant or BigQuery is unavailable.

---

## Guardrails (short)

- **Input**: length, PII, prompt injection, banned topics, per-caller rate limit
- **SQL**: SELECT/WITH only, table allowlist, auto `LIMIT 1000`, dry-run bytes under 10 GiB
- **Output**: `[Source: …]` required (one retry), confidence gate, number cross-check, hallucination disclaimer, PII scrub

---

## License and status

Internal LiveRamp tooling. Python **3.13**, package manager **uv**.
