"""Typed prompt constants for the LangGraph agent."""

from __future__ import annotations

from typing import Final

CLASSIFY_INTENT_PROMPT: Final[
    str
] = """You are an intent classifier for LiveRamp syndicated segment questions.

Choose exactly one label:
- analytics: rankings, totals, comparisons, "top segments", numeric BigQuery metrics,
  platform activation counts, distribution breadth, segments activated on a platform,
  reach by device type, highly-distributed/highly-reachable filters
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

The runtime injects repository file best_sellers.sql as a CTE named
bestsellers_segments. That CTE is the only data source. There is no physical
table called bestsellers_segments, bestsellers, or any other short name.

Rules:
- Your response is a structured JSON object with a single "sql" field.
- The "sql" field must contain a SELECT or WITH ... SELECT statement only.
- Never INSERT, UPDATE, DELETE, DROP, or DDL.
- Always include LIMIT 1000 unless a smaller LIMIT is present.
- Query ONLY bestsellers_segments. Do not reference other tables, datasets,
  or fully-qualified names. Do not paste or rewrite best_sellers.sql.
- Prefer a single SELECT against bestsellers_segments (filters, ORDER BY, LIMIT).
- ALWAYS include these columns in every SELECT (they are required for the API response):
    dms_segment_id, segment_name, segment_description,
    active_platform_names, active_buyers, active_platforms,
    distribution_rank, reach_rank
  Add extra columns on top of these as the question requires.

Available columns on bestsellers_segments:
  dms_segment_id, segment_name, segment_description, segment_type, seller_customer_id,
  active_destination_accounts, active_buyers, active_platforms, active_platform_names,
  cookie_reach, ios_reach, android_reach, input_records,
  cookie_reach_updated_at, ios_reach_updated_at, android_reach_updated_at,
  reach_by_platform, distribution_rank, reach_rank,
  is_highly_distributed, is_highly_reachable, is_top_n_by_reach

Platform filtering rules:
{platform_hint}

Examples:
  -- Top segments by distribution rank
  SELECT dms_segment_id, segment_name, segment_description,
         active_platform_names, active_buyers, active_platforms,
         distribution_rank, reach_rank
  FROM bestsellers_segments
  ORDER BY distribution_rank ASC
  LIMIT 1000

  -- Top segments activated to The Trade Desk (canonical name known)
  SELECT dms_segment_id, segment_name, segment_description,
         active_platform_names, active_buyers, active_platforms,
         distribution_rank, reach_rank
  FROM bestsellers_segments
  WHERE LOWER(active_platform_names) LIKE '%the trade desk%'
  ORDER BY distribution_rank ASC
  LIMIT 1000

  -- Top segments activated to tradedesk (name not resolved, use REGEXP_REPLACE)
  SELECT dms_segment_id, segment_name, segment_description,
         active_platform_names, active_buyers, active_platforms,
         distribution_rank, reach_rank
  FROM bestsellers_segments
  WHERE REGEXP_REPLACE(LOWER(active_platform_names), r'[^a-z0-9]', '')
        LIKE CONCAT('%', REGEXP_REPLACE(LOWER('tradedesk'), r'[^a-z0-9]', ''), '%')
  ORDER BY distribution_rank ASC
  LIMIT 1000

  -- Segments at a specific distribution rank
  SELECT dms_segment_id, segment_name, segment_description,
         active_platform_names, active_buyers, active_platforms,
         distribution_rank, reach_rank
  FROM bestsellers_segments
  WHERE distribution_rank = 12
  LIMIT 1000

  -- Top 10% by cookie reach
  SELECT dms_segment_id, segment_name, segment_description,
         active_platform_names, active_buyers, active_platforms,
         distribution_rank, reach_rank, cookie_reach
  FROM bestsellers_segments
  WHERE is_highly_reachable = TRUE
  ORDER BY reach_rank ASC
  LIMIT 1000

  -- Top 10% by distribution
  SELECT dms_segment_id, segment_name, segment_description,
         active_platform_names, active_buyers, active_platforms,
         distribution_rank, reach_rank
  FROM bestsellers_segments
  WHERE is_highly_distributed = TRUE
  ORDER BY distribution_rank ASC
  LIMIT 1000

  -- Active across 8 or more platforms
  SELECT dms_segment_id, segment_name, segment_description,
         active_platform_names, active_buyers, active_platforms,
         distribution_rank, reach_rank
  FROM bestsellers_segments
  WHERE active_platforms >= 8
  ORDER BY active_platforms DESC
  LIMIT 1000

  -- Used by 23 or more buyers
  SELECT dms_segment_id, segment_name, segment_description,
         active_platform_names, active_buyers, active_platforms,
         distribution_rank, reach_rank
  FROM bestsellers_segments
  WHERE active_buyers >= 23
  ORDER BY active_buyers DESC
  LIMIT 1000

  -- Compare reach metrics for the top five
  SELECT dms_segment_id, segment_name, segment_description,
         active_platform_names, active_buyers, active_platforms,
         distribution_rank, reach_rank,
         cookie_reach, ios_reach, android_reach, active_destination_accounts
  FROM bestsellers_segments
  ORDER BY distribution_rank ASC
  LIMIT 5

  -- Exclude segments from a specific seller
  SELECT dms_segment_id, segment_name, segment_description,
         active_platform_names, active_buyers, active_platforms,
         distribution_rank, reach_rank
  FROM bestsellers_segments
  WHERE seller_customer_id != '99999'
  ORDER BY distribution_rank ASC
  LIMIT 1000

User question:
{query}

SQL:"""

_PLATFORM_HINT_WITH_NAME: Final[str] = (
    "A canonical platform name has been resolved: '{platform_name}'.\n"
    "Use: WHERE LOWER(active_platform_names) LIKE '%{platform_name_lower}%'"
)

_PLATFORM_HINT_NO_NAME: Final[str] = (
    "No canonical platform name was resolved. If the user mentions a platform, "
    "normalise both sides with REGEXP_REPLACE:\n"
    "  WHERE REGEXP_REPLACE(LOWER(active_platform_names), r'[^a-z0-9]', '')\n"
    "        LIKE CONCAT('%', REGEXP_REPLACE(LOWER('<user_input>'), r'[^a-z0-9]', ''), '%')\n"
    "Replace <user_input> with the user's platform mention verbatim."
)


def build_platform_hint(canonical: str | None) -> str:
    """Format the platform-hint block for injection into TEXT2SQL_PROMPT.

    Args:
        canonical: Resolved canonical platform name, or ``None``.

    Returns:
        Rendered hint string for the ``{platform_hint}`` slot.
    """
    if canonical:
        return _PLATFORM_HINT_WITH_NAME.format(
            platform_name=canonical,
            platform_name_lower=canonical.lower(),
        )
    return _PLATFORM_HINT_NO_NAME


SQL_RETRY_PROMPT: Final[
    str
] = """The SQL you generated returned 0 rows for the platform filter.

Original query: {query}
Platform value used in SQL: {platform_used}

Known canonical platform names in the data:
{platform_list}

Rewrite the SQL using one of the canonical names above (exact case as shown).
Use: WHERE LOWER(active_platform_names) LIKE '%<canonical name lowercased>%'

Rules (same as before):
- SELECT or WITH ... SELECT only.
- Query ONLY bestsellers_segments.
- Always include LIMIT 1000 unless a smaller LIMIT is present.

Rewritten SQL:"""

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
