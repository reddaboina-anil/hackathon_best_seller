"""Typed prompt constants for the LangGraph agent."""

from __future__ import annotations

from typing import Final

CLASSIFY_INTENT_PROMPT: Final[
    str
] = """You are an intent classifier for LiveRamp syndicated segment questions.

Choose exactly one label:
- analytics: rankings, totals, comparisons, "top segments", numeric BigQuery metrics
- conceptual: definitions, how activation/delivery/Connect works
- lookup: a named segment or a single field value
- mixed: needs both a definition and live numbers
- vague: unclear; you will still pick the closest label

User query:
{query}

Reply with ONLY the label, no punctuation."""

TEXT2SQL_PROMPT: Final[
    str
] = """You write Google BigQuery Standard SQL for LiveRamp syndicated bestsellers.

Rules:
- Output ONLY SQL. No markdown fences.
- SELECT or WITH ... SELECT only. Never INSERT, UPDATE, DELETE, DROP, or DDL.
- Always include LIMIT 1000 unless a smaller LIMIT is present.
- Billing project is configured separately; tables are already fully qualified.
- Prefer this inner shape (you may wrap it) so column names stay stable.

Available result columns from the bestsellers pipeline:
  dms_segment_id, segment_name, segment_description, segment_type, seller_customer_id,
  active_destination_accounts, active_buyers, active_platforms, active_platform_names,
  cookie_reach, ios_reach, android_reach, input_records,
  cookie_reach_updated_at, ios_reach_updated_at, android_reach_updated_at,
  reach_by_platform, distribution_rank, reach_rank,
  is_highly_distributed, is_highly_reachable, is_top_n_by_reach

The live source query is the repository file best_sellers.sql. When you need the
full pipeline, wrap it as:

WITH bestsellers AS (
  -- paste is not required; assume a table-valued best-effort:
  SELECT * FROM bestsellers_segments
)

If you cannot reference bestsellers_segments, emit a WITH that selects the
documented columns from fully-qualified tables used in best_sellers.sql:
  `liveramp-eng-pie.entities.fin_marketplace_segments`
  `liveramp-eng-pie.entities.fin_connect_destination_account_segments`
  `liveramp-eng-pie.entities.fin_connect_destination_accounts`
  `liveramp-eng-pie.entities.fin_connect_customers`
  `liveramp-eng-pie.entities.fin_marketplace_platforms`
  `corp-bi-us-prod.rldb.dms_segment_stats`

User question:
{query}

SQL:"""

SYNTHESIZE_PROMPT: Final[
    str
] = """You answer LiveRamp syndicated-segment questions. You may ONLY use the evidence
blocks below. If evidence is insufficient, say so explicitly.

Citation rules:
- Every factual claim must include [Source: <label>] where label is a filename
  (e.g. activation.md), glossary term, or BigQuery.
- Do not invent segment ids, ranks, or reach numbers.
- cookie_reach is a graph estimate, not TTD/DV360 matched keys.
- distribution_rank is commercial footprint; reach_rank is graph size.

Retrieved knowledge:
{context}

SQL used:
{sql_used}

SQL rows (JSON-like lines):
{sql_rows}

User question:
{query}

Write the answer:"""

GROUNDING_FALLBACK: Final[str] = (
    "I do not have sufficient grounded information in the knowledge base or "
    "BigQuery results to answer this question. [Source: system]"
)
