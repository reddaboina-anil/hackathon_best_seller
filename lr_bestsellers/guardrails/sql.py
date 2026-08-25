"""SQL guardrails: SELECT-only, table allowlist, LIMIT, cost estimate."""

from __future__ import annotations

import re
from typing import Final, Protocol, runtime_checkable

from lr_bestsellers.guardrails.base import GuardrailResult
from lr_bestsellers.models.query import BqQueryRequest

_FORBIDDEN: Final[tuple[str, ...]] = (
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "MERGE",
    "TRUNCATE",
    "ALTER",
    "CREATE",
    "GRANT",
    "REVOKE",
    "EXECUTE",
    "CALL",
)
_TABLE_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:FROM|JOIN)\s+(`?[A-Za-z0-9_.-]+`?)",
    re.IGNORECASE,
)
_LIMIT_RE: Final[re.Pattern[str]] = re.compile(r"\bLIMIT\s+\d+\b", re.IGNORECASE)

ALLOWED_TABLES: Final[frozenset[str]] = frozenset(
    {
        "liveramp-eng-pie.entities.fin_marketplace_segments",
        "liveramp-eng-pie.entities.fin_connect_destination_account_segments",
        "liveramp-eng-pie.entities.fin_connect_destination_accounts",
        "liveramp-eng-pie.entities.fin_connect_customers",
        "liveramp-eng-pie.entities.fin_marketplace_platforms",
        "corp-bi-us-prod.rldb.dms_segment_stats",
        "bestsellers_segments",
    }
)
_MAX_BYTES: Final[int] = 10 * 1024 * 1024 * 1024


def _strip_sql_comments(sql: str) -> str:
    """Remove ``--`` and ``/* */`` comments.

    Args:
        sql: Raw SQL.

    Returns:
        SQL without comments.
    """
    without_block = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    lines = [re.sub(r"--.*?$", "", line) for line in without_block.splitlines()]
    return "\n".join(lines)


def _mask_sql_literals(sql: str) -> str:
    """Replace quoted string bodies with spaces.

    BigQuery ``STRING_AGG`` in ``best_sellers.sql`` uses ``'; '`` as a
    separator. A raw ``;`` check would treat that as a second statement.

    Args:
        sql: SQL text, typically after comment stripping.

    Returns:
        Copy of ``sql`` with characters inside ``'...'`` / ``"..."``
        replaced by spaces. Doubled quotes (``''``) are treated as escapes.
    """
    chars: list[str] = []
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        if ch not in {"'", '"'}:
            chars.append(ch)
            i += 1
            continue
        quote = ch
        chars.append(" ")
        i += 1
        while i < n:
            cur = sql[i]
            if cur == quote:
                i += 1
                if i < n and sql[i] == quote:
                    chars.append(" ")
                    i += 1
                    continue
                chars.append(" ")
                break
            chars.append(" ")
            i += 1
    return "".join(chars)


def _normalize_table(name: str) -> str:
    """Strip backticks from a table identifier.

    Args:
        name: Raw table token.

    Returns:
        Unquoted name.
    """
    return name.replace("`", "").strip()


@runtime_checkable
class BytesEstimatorProtocol(Protocol):
    """Dry-run bytes estimator for BigQuery."""

    def estimate_bytes(self, request: BqQueryRequest) -> int:
        """Return estimated bytes processed.

        Args:
            request: SQL to dry-run.

        Returns:
            Estimated bytes.
        """
        ...


class FakeBytesEstimator:
    """Returns a configured byte estimate (tests).

    Args:
        nbytes: Value returned by :meth:`estimate_bytes`.
    """

    def __init__(self, nbytes: int = 1024) -> None:
        """Store the canned estimate.

        Args:
            nbytes: Fake processed bytes.
        """
        self.nbytes = nbytes

    def estimate_bytes(self, request: BqQueryRequest) -> int:
        """Return the canned estimate.

        Args:
            request: Unused SQL.

        Returns:
            Canned byte count.
        """
        del request
        return self.nbytes


class BigQueryBytesEstimator:
    """Live BigQuery dry-run estimator.

    Args:
        client: ``bigquery.Client``.
    """

    def __init__(self, client: object) -> None:
        """Store the client.

        Args:
            client: BigQuery client.
        """
        self._client = client

    def estimate_bytes(self, request: BqQueryRequest) -> int:
        """Run a dry-run job and return ``total_bytes_processed``.

        Args:
            request: SQL to estimate.

        Returns:
            Estimated bytes (0 if the API omits the field).
        """
        from google.cloud import bigquery

        job_config = bigquery.QueryJobConfig(dry_run=True, use_query_cache=False)
        query = getattr(self._client, "query")
        job = query(request.sql, job_config=job_config)
        return int(getattr(job, "total_bytes_processed", 0) or 0)


class SelectOnlyGuardrail:
    """Allow only a single SELECT/WITH statement."""

    @property
    def name(self) -> str:
        """Return ``select_only``."""
        return "select_only"

    def check(self, value: str) -> GuardrailResult:
        """Reject non-SELECT statements and stacked queries.

        Args:
            value: SQL text.

        Returns:
            Fail with ``SQL_NOT_SELECT``.
        """
        cleaned = _strip_sql_comments(value).strip().rstrip(";")
        if not cleaned:
            return GuardrailResult(passed=False, code="SQL_NOT_SELECT", message="Empty SQL")
        if ";" in _mask_sql_literals(cleaned):
            return GuardrailResult(
                passed=False,
                code="SQL_NOT_SELECT",
                message="Multiple SQL statements are not allowed",
            )
        head = cleaned.split(None, 1)[0].upper()
        if head not in {"SELECT", "WITH"}:
            return GuardrailResult(
                passed=False,
                code="SQL_NOT_SELECT",
                message="SQL must be SELECT or WITH",
            )
        upper = cleaned.upper()
        for word in _FORBIDDEN:
            if re.search(rf"\b{word}\b", upper):
                return GuardrailResult(
                    passed=False,
                    code="SQL_NOT_SELECT",
                    message=f"Forbidden keyword {word}",
                )
        return GuardrailResult(passed=True, rewritten=value)


class TableAllowlistGuardrail:
    """Allow only known fully-qualified bestsellers tables (plus CTE names).

    Args:
        allowed: Override allowlist.
    """

    def __init__(self, allowed: frozenset[str] | None = None) -> None:
        """Store the allowlist.

        Args:
            allowed: Permitted table names.
        """
        self._allowed = allowed or ALLOWED_TABLES

    @property
    def name(self) -> str:
        """Return ``table_allowlist``."""
        return "table_allowlist"

    def check(self, value: str) -> GuardrailResult:
        """Ensure every FROM/JOIN target is allowlisted or a CTE.

        Args:
            value: SQL text.

        Returns:
            Fail with ``DISALLOWED_TABLE``.
        """
        cleaned = _strip_sql_comments(value)
        cte_names = {
            match.group(1).lower()
            for match in re.finditer(
                r"(?:(?:WITH|,)\s+)([A-Za-z_][A-Za-z0-9_]*)\s+AS\s*\(",
                cleaned,
                flags=re.IGNORECASE,
            )
        }
        for match in _TABLE_RE.finditer(cleaned):
            table = _normalize_table(match.group(1))
            if table.lower() in cte_names:
                continue
            if table not in self._allowed and table.lower() not in {
                item.lower() for item in self._allowed
            }:
                return GuardrailResult(
                    passed=False,
                    code="DISALLOWED_TABLE",
                    message=f"Table not on allowlist: {table}",
                )
        return GuardrailResult(passed=True, rewritten=value)


class RowLimitGuardrail:
    """Append ``LIMIT 1000`` when the query has no LIMIT.

    Args:
        max_rows: Limit to append.
    """

    def __init__(self, max_rows: int = 1000) -> None:
        """Store the row cap.

        Args:
            max_rows: LIMIT value.
        """
        self._max_rows = max_rows

    @property
    def name(self) -> str:
        """Return ``row_limit``."""
        return "row_limit"

    def check(self, value: str) -> GuardrailResult:
        """Auto-fix missing LIMIT.

        Args:
            value: SQL text.

        Returns:
            Always passes; may set ``rewritten``.
        """
        if _LIMIT_RE.search(value):
            return GuardrailResult(passed=True, rewritten=value)
        rewritten = value.rstrip().rstrip(";") + f"\nLIMIT {self._max_rows}"
        return GuardrailResult(passed=True, rewritten=rewritten, code="LIMIT_APPENDED")


class CostEstimationGuardrail:
    """Reject queries whose dry-run bytes exceed 10 GiB.

    Args:
        estimator: Bytes estimator.
        max_bytes: Cost ceiling.
    """

    def __init__(
        self,
        estimator: BytesEstimatorProtocol | None = None,
        max_bytes: int = _MAX_BYTES,
    ) -> None:
        """Store estimator and ceiling.

        Args:
            estimator: Dry-run implementation.
            max_bytes: Maximum allowed bytes.
        """
        self._estimator = estimator or FakeBytesEstimator()
        self._max_bytes = max_bytes

    @property
    def name(self) -> str:
        """Return ``cost_estimation``."""
        return "cost_estimation"

    def check(self, value: str) -> GuardrailResult:
        """Dry-run the SQL.

        Args:
            value: SQL text.

        Returns:
            Fail with ``QUERY_TOO_EXPENSIVE``.
        """
        nbytes = self._estimator.estimate_bytes(BqQueryRequest(sql=value))
        if nbytes > self._max_bytes:
            return GuardrailResult(
                passed=False,
                code="QUERY_TOO_EXPENSIVE",
                message=f"Estimated {nbytes} bytes exceeds {self._max_bytes}",
            )
        return GuardrailResult(passed=True, rewritten=value)
