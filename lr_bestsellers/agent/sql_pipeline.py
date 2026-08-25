"""CTE-merging assembler for ``best_sellers.sql`` + LLM query.

BigQuery Standard SQL **never allows ``WITH`` inside a subquery expression**.
This module merges the pipeline CTEs with the LLM SELECT so the final
statement is a single top-level ``WITH … SELECT``.

Assembly strategy
-----------------
Given ``pipeline_sql`` that looks like::

    WITH syndicated_segments AS (…),
    classified_segments AS (…)
    SELECT … FROM classified_segments WHERE …

and an LLM SQL::

    SELECT … FROM bestsellers_segments WHERE … ORDER BY … LIMIT 1000

the output is::

    WITH syndicated_segments AS (…),
    classified_segments AS (…),
    bestsellers_segments AS (
      SELECT … FROM classified_segments WHERE …   ← pipeline final SELECT
    )
    SELECT … FROM bestsellers_segments WHERE … ORDER BY … LIMIT 1000

If the LLM SQL *itself* starts with ``WITH``, its CTEs are appended after
the pipeline CTEs and its final SELECT is used as the outer query.
"""

from __future__ import annotations

import re
from typing import Final

import structlog

log = structlog.get_logger(__name__)

_WITH_RE: Final[re.Pattern[str]] = re.compile(r"^\s*WITH\b", re.IGNORECASE)

_LINE_COMMENT_RE: Final[re.Pattern[str]] = re.compile(r"--[^\n]*", re.MULTILINE)
_BLOCK_COMMENT_RE: Final[re.Pattern[str]] = re.compile(r"/\*.*?\*/", re.DOTALL)


def _first_keyword_is_with(sql: str) -> bool:
    """Return ``True`` when the first real SQL keyword in ``sql`` is ``WITH``.

    Strips ``--`` line comments and ``/* */`` block comments before checking,
    so files like ``best_sellers.sql`` that open with a comment header are
    handled correctly.

    Args:
        sql: SQL text (may have leading comments).

    Returns:
        ``True`` when the first non-comment token is the ``WITH`` keyword.
    """
    stripped = _LINE_COMMENT_RE.sub("", sql)
    stripped = _BLOCK_COMMENT_RE.sub("", stripped)
    return bool(_WITH_RE.match(stripped.lstrip()))


def _find_top_level_select(sql: str) -> int:
    """Return the byte offset of the last depth-0 ``SELECT`` keyword.

    The scanner is parenthesis-balanced and skips string literals
    (single-quoted, double-quoted, backtick-quoted) and both ``--`` line
    comments and ``/* */`` block comments.

    Args:
        sql: SQL text to scan.

    Returns:
        Character offset of the final depth-0 ``SELECT``, or ``-1`` if none
        is found.
    """
    depth = 0
    i = 0
    n = len(sql)
    last_select = -1

    while i < n:
        ch = sql[i]

        # Skip line comments
        if ch == "-" and i + 1 < n and sql[i + 1] == "-":
            while i < n and sql[i] != "\n":
                i += 1
            continue

        # Skip block comments
        if ch == "/" and i + 1 < n and sql[i + 1] == "*":
            i += 2
            while i + 1 < n and not (sql[i] == "*" and sql[i + 1] == "/"):
                i += 1
            i += 2
            continue

        # Skip quoted strings (single, double, backtick)
        if ch in {"'", '"', "`"}:
            quote = ch
            i += 1
            while i < n:
                cur = sql[i]
                if cur == "\\" and quote != "`":
                    i += 2
                    continue
                if cur == quote:
                    i += 1
                    break
                i += 1
            continue

        if ch == "(":
            depth += 1
            i += 1
            continue

        if ch == ")":
            depth -= 1
            i += 1
            continue

        # Check for SELECT at depth 0
        if depth == 0 and sql[i : i + 6].upper() == "SELECT":
            # Ensure it's a word boundary after SELECT
            after = sql[i + 6] if i + 6 < n else " "
            if not (after.isalnum() or after == "_"):
                last_select = i
        i += 1

    return last_select


def _split_pipeline(pipeline_sql: str) -> tuple[str, str]:
    """Split ``best_sellers.sql`` body into CTE block + final SELECT.

    Args:
        pipeline_sql: Full pipeline SQL (no trailing semicolon).

    Returns:
        ``(cte_block, final_select)`` where ``cte_block`` includes the ``WITH``
        keyword and all CTE definitions up to (but not including) the final
        depth-0 SELECT, and ``final_select`` is that SELECT and everything after.

    Raises:
        ValueError: When no depth-0 SELECT is found in ``pipeline_sql``.
    """
    offset = _find_top_level_select(pipeline_sql)
    if offset == -1:
        raise ValueError("No depth-0 SELECT found in pipeline SQL")
    cte_block = pipeline_sql[:offset].rstrip().rstrip(",").rstrip()
    final_select = pipeline_sql[offset:]
    return cte_block, final_select


def assemble_bestsellers_query(llm_sql: str, pipeline_sql: str) -> str:
    """Merge pipeline CTEs with the LLM SELECT into valid BigQuery SQL.

    BigQuery requires all CTEs at the top level — ``WITH`` inside a subquery
    expression is a syntax error. This function handles four cases:

    1. **Happy path** — ``pipeline_sql`` has CTEs + final SELECT, LLM has a
       plain SELECT: pipeline CTEs + ``bestsellers_segments AS (final_select)``
       + LLM SELECT.
    2. **LLM WITH** — LLM itself starts with ``WITH``: split the LLM CTEs,
       append them after the pipeline CTEs, use LLM final SELECT as outer.
    3. **Empty pipeline** — return ``llm_sql`` unchanged (unit-test path).
    4. **No depth-0 SELECT in pipeline** — log a warning, return ``llm_sql``
       unchanged (graceful degradation).

    Args:
        llm_sql: SELECT or WITH … SELECT generated by the LLM that references
            ``bestsellers_segments``.
        pipeline_sql: Body of ``best_sellers.sql`` (no trailing semicolon).

    Returns:
        A single valid BigQuery SQL statement.
    """
    pipeline = pipeline_sql.strip().rstrip(";")
    llm = llm_sql.strip().rstrip(";")

    if not pipeline:
        return llm

    # Determine whether the pipeline SQL has a WITH block at all.
    if not _first_keyword_is_with(pipeline):
        # No CTEs — pipeline is just a bare SELECT; wrap as derived table for
        # backward compatibility (shouldn't happen with best_sellers.sql).
        log.warning(
            "sql_pipeline.no_cte_block",
            note="Pipeline SQL has no WITH clause; falling back to subquery wrap",
        )
        derived = f"(\n{pipeline}\n) AS bestsellers_segments"
        replaced = re.sub(
            r"\bFROM\s+bestsellers_segments\b",
            f"FROM {derived}",
            llm,
            count=1,
            flags=re.IGNORECASE,
        )
        return replaced if replaced != llm else f"SELECT * FROM {derived}\nLIMIT 1000"

    try:
        cte_block, pipeline_final_select = _split_pipeline(pipeline)
    except ValueError:
        log.warning(
            "sql_pipeline.no_top_level_select",
            note="Could not find depth-0 SELECT in pipeline; returning llm_sql unchanged",
        )
        return llm

    if _first_keyword_is_with(llm):
        # The LLM produced its own WITH block.  Merge CTEs then use LLM's
        # final SELECT as the outer query.
        try:
            llm_cte_block, llm_final_select = _split_pipeline(llm)
        except ValueError:
            # LLM WITH without a parseable final SELECT — degrade gracefully.
            log.warning(
                "sql_pipeline.llm_with_no_select",
                note="LLM WITH had no parseable final SELECT; using plain assembly",
            )
            llm_final_select = llm
            llm_cte_block = ""

        # Strip the leading WITH from the LLM CTE block so we can join as
        # additional CTE definitions.
        if llm_cte_block:
            llm_extra_ctes = re.sub(r"^\s*WITH\s+", "", llm_cte_block, count=1, flags=re.IGNORECASE)
            llm_extra_ctes = llm_extra_ctes.rstrip().rstrip(",")
            merged_ctes = (
                f"{cte_block},\n"
                f"bestsellers_segments AS (\n{pipeline_final_select}\n),\n"
                f"{llm_extra_ctes}"
            )
        else:
            merged_ctes = (
                f"{cte_block},\n"
                f"bestsellers_segments AS (\n{pipeline_final_select}\n)"
            )

        return f"{merged_ctes}\n{llm_final_select}"

    # Happy path: plain LLM SELECT.
    return (
        f"{cte_block},\n"
        f"bestsellers_segments AS (\n{pipeline_final_select}\n)\n"
        f"{llm}"
    )
