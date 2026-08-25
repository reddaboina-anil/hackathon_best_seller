"""Deterministic assembly tests for all 10 supported user query patterns.

Each test assembles a canned LLM SQL against a minimal 2-CTE pipeline fixture
and verifies:
- Assembled SQL starts with ``WITH`` (CTEs at top level).
- No nested ``WITH`` inside a subquery expression.
- ``bestsellers_segments AS (`` is present.
- All pattern-specific substrings are present.
- ``SelectOnlyGuardrail`` passes.
- ``TableAllowlistGuardrail`` passes.

The patterns cover the 10 main query types documented in the plan.
"Show more segments like the second result" is excluded (requires conversation
state; system is stateless per query).
"""

from __future__ import annotations

import pytest

from lr_bestsellers.agent.sql_pipeline import assemble_bestsellers_query
from lr_bestsellers.guardrails.sql import SelectOnlyGuardrail, TableAllowlistGuardrail

# ---------------------------------------------------------------------------
# Minimal 2-CTE pipeline fixture
# ---------------------------------------------------------------------------

MINIMAL_PIPELINE_SQL = """\
WITH base AS (
  SELECT
    1 AS dms_segment_id,
    'Test Segment' AS segment_name,
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
    NULL AS reach_by_platform,
    'standard' AS segment_type,
    'desc' AS segment_description,
    1 AS distribution_rank,
    1 AS reach_rank
),
classified AS (
  SELECT
    *,
    TRUE AS is_highly_distributed,
    FALSE AS is_highly_reachable,
    FALSE AS is_top_n_by_reach
  FROM base
)
SELECT
  dms_segment_id, segment_name, segment_description, segment_type,
  seller_customer_id, active_destination_accounts, active_buyers,
  active_platforms, active_platform_names, cookie_reach, ios_reach,
  android_reach, input_records, cookie_reach_updated_at,
  ios_reach_updated_at, android_reach_updated_at, reach_by_platform,
  distribution_rank, reach_rank, is_highly_distributed,
  is_highly_reachable, is_top_n_by_reach
FROM classified
WHERE is_highly_distributed"""

# ---------------------------------------------------------------------------
# Guardrail instances shared across all tests
# ---------------------------------------------------------------------------

_SELECT_GUARDRAIL = SelectOnlyGuardrail()
_TABLE_GUARDRAIL = TableAllowlistGuardrail()


def _assemble(llm_sql: str) -> str:
    """Assemble ``llm_sql`` against the minimal pipeline.

    Args:
        llm_sql: LLM-generated SELECT targeting ``bestsellers_segments``.

    Returns:
        Assembled BigQuery SQL statement.
    """
    return assemble_bestsellers_query(llm_sql, MINIMAL_PIPELINE_SQL)


def _assert_common(assembled: str) -> None:
    """Run invariant checks shared by every query pattern.

    Args:
        assembled: Assembled SQL to validate.
    """
    upper = assembled.strip().upper()
    # 1. Starts with WITH (CTEs at top level).
    assert upper.startswith("WITH"), f"Expected WITH at top; got: {assembled[:80]}"
    # 2. No nested WITH inside a subquery.
    assert "(\nWITH" not in assembled, "Nested WITH found in subquery context"
    no_newline = assembled.replace("\n", " ")
    assert "( WITH" not in no_newline.upper(), "Nested WITH found (single-line variant)"
    # 3. bestsellers_segments CTE present.
    assert "bestsellers_segments AS (" in assembled, "bestsellers_segments CTE not found"
    # 4. Guardrails pass.
    select_result = _SELECT_GUARDRAIL.check(assembled)
    assert select_result.passed, f"SelectOnlyGuardrail failed: {select_result.message}"
    table_result = _TABLE_GUARDRAIL.check(assembled)
    assert table_result.passed, f"TableAllowlistGuardrail failed: {table_result.message}"


# ---------------------------------------------------------------------------
# Query-pattern parametrise data
# ---------------------------------------------------------------------------

QUERY_PATTERNS: list[tuple[str, str, list[str]]] = [
    (
        "pattern_1_canonical_platform",
        (
            "SELECT dms_segment_id, segment_name, active_platform_names, distribution_rank\n"
            "FROM bestsellers_segments\n"
            "WHERE LOWER(active_platform_names) LIKE '%the trade desk%'\n"
            "ORDER BY distribution_rank ASC\n"
            "LIMIT 1000"
        ),
        ["LIKE '%the trade desk%'"],
    ),
    (
        "pattern_2_regexp_platform",
        (
            "SELECT dms_segment_id, segment_name, active_platform_names, distribution_rank\n"
            "FROM bestsellers_segments\n"
            "WHERE REGEXP_REPLACE(LOWER(active_platform_names), r'[^a-z0-9]', '') "
            "LIKE '%tradedesk%'\n"
            "LIMIT 1000"
        ),
        ["REGEXP_REPLACE", "tradedesk"],
    ),
    (
        "pattern_3_rank_lookup",
        (
            "SELECT dms_segment_id, segment_name, distribution_rank\n"
            "FROM bestsellers_segments\n"
            "WHERE distribution_rank = 12\n"
            "LIMIT 1000"
        ),
        ["distribution_rank = 12"],
    ),
    (
        "pattern_4_top10pct_impressions",
        (
            "SELECT dms_segment_id, segment_name, cookie_reach, reach_rank\n"
            "FROM bestsellers_segments\n"
            "WHERE is_highly_reachable = TRUE\n"
            "ORDER BY reach_rank ASC\n"
            "LIMIT 1000"
        ),
        ["is_highly_reachable"],
    ),
    (
        "pattern_5_top10pct_revenue",
        (
            "SELECT dms_segment_id, segment_name, distribution_rank\n"
            "FROM bestsellers_segments\n"
            "WHERE is_highly_distributed = TRUE\n"
            "ORDER BY distribution_rank ASC\n"
            "LIMIT 1000"
        ),
        ["is_highly_distributed"],
    ),
    (
        "pattern_6_active_platforms_count",
        (
            "SELECT dms_segment_id, segment_name, active_platforms\n"
            "FROM bestsellers_segments\n"
            "WHERE active_platforms >= 8\n"
            "ORDER BY active_platforms DESC\n"
            "LIMIT 1000"
        ),
        ["active_platforms >= 8"],
    ),
    (
        "pattern_7_buyer_count",
        (
            "SELECT dms_segment_id, segment_name, active_buyers\n"
            "FROM bestsellers_segments\n"
            "WHERE active_buyers >= 23\n"
            "ORDER BY active_buyers DESC\n"
            "LIMIT 1000"
        ),
        # Note: active_buyers is point-in-time only; no time-window in schema.
        ["active_buyers >= 23"],
    ),
    (
        "pattern_8_show_top10pct_reach",
        (
            "SELECT dms_segment_id, segment_name, cookie_reach, reach_rank\n"
            "FROM bestsellers_segments\n"
            "WHERE is_highly_reachable = TRUE\n"
            "ORDER BY reach_rank ASC\n"
            "LIMIT 1000"
        ),
        ["is_highly_reachable"],
    ),
    (
        "pattern_9_compare_top5",
        (
            "SELECT dms_segment_id, segment_name, cookie_reach, active_destination_accounts,\n"
            "       distribution_rank, reach_rank\n"
            "FROM bestsellers_segments\n"
            "ORDER BY distribution_rank ASC\n"
            "LIMIT 5"
        ),
        ["LIMIT 5"],
    ),
    (
        "pattern_10_exclude_seller",
        (
            "SELECT dms_segment_id, segment_name, distribution_rank\n"
            "FROM bestsellers_segments\n"
            "WHERE seller_customer_id != '99999'\n"
            "ORDER BY distribution_rank ASC\n"
            "LIMIT 1000"
        ),
        ["seller_customer_id"],
    ),
]


@pytest.mark.parametrize("name,llm_sql,must_contain", QUERY_PATTERNS, ids=[p[0] for p in QUERY_PATTERNS])
def test_query_pattern(name: str, llm_sql: str, must_contain: list[str]) -> None:
    """Assembled SQL for each query pattern passes all structural invariants.

    Args:
        name: Pattern identifier (unused by test logic; used for pytest id).
        llm_sql: Canned LLM SQL for the pattern.
        must_contain: Substrings that must appear in the assembled SQL.
    """
    del name  # only used as pytest id
    assembled = _assemble(llm_sql)
    _assert_common(assembled)
    for substring in must_contain:
        assert substring in assembled, (
            f"Expected '{substring}' in assembled SQL.\nAssembled:\n{assembled}"
        )


class TestAssembleLlmWith:
    """Edge cases when the LLM produces its own WITH block."""

    def test_llm_with_is_merged_at_top_level(self) -> None:
        """LLM WITH is merged; assembled SQL has exactly one top-level WITH."""
        llm_sql = (
            "WITH rank_filter AS (\n"
            "  SELECT * FROM bestsellers_segments WHERE distribution_rank <= 5\n"
            ")\n"
            "SELECT * FROM rank_filter LIMIT 5"
        )
        assembled = _assemble(llm_sql)
        _assert_common(assembled)
        assert "rank_filter AS (" in assembled
        assert "SELECT * FROM rank_filter LIMIT 5" in assembled

    def test_no_double_with_in_output(self) -> None:
        """Assembled SQL never contains WITH inside a FROM () block."""
        llm_sql = "SELECT * FROM bestsellers_segments ORDER BY distribution_rank LIMIT 1000"
        assembled = _assemble(llm_sql)
        assert "FROM (\nWITH" not in assembled
        # No WITH preceded by an opening paren
        assert "(WITH" not in assembled.replace(" ", "").replace("\n", "")


class TestCommentHeaderPipeline:
    """Pipeline SQL with leading comment lines (mirrors real best_sellers.sql)."""

    def test_comment_header_is_handled(self) -> None:
        """Pipeline SQL that opens with -- comments assembles correctly."""
        pipeline_with_comments = (
            "-- BigQuery Standard SQL\n"
            "-- Top 10% inlined.\n\n"
            + MINIMAL_PIPELINE_SQL
        )
        llm_sql = (
            "SELECT dms_segment_id, segment_name FROM bestsellers_segments LIMIT 10"
        )
        assembled = assemble_bestsellers_query(llm_sql, pipeline_with_comments)
        # Comments before WITH are valid SQL — the first non-comment token is WITH.
        assert "WITH" in assembled.upper()
        assert "bestsellers_segments AS (" in assembled
        # The old broken pattern must not appear.
        assert "FROM (\nWITH" not in assembled
        assert "FROM (\n-- BigQuery" not in assembled
        select_result = _SELECT_GUARDRAIL.check(assembled)
        assert select_result.passed
        table_result = _TABLE_GUARDRAIL.check(assembled)
        assert table_result.passed
