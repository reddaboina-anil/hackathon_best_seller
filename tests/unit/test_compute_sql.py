"""Deterministic tests for ``compute_tags.sql``.

Loads the SQL into an in-memory DuckDB against a 5-row TSV fixture so tag
rules can be asserted without Docker or BigQuery.
"""

from __future__ import annotations

import csv
from collections.abc import Iterator
from pathlib import Path
from typing import Final

import duckdb
import pytest

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
COMPUTE_SQL_PATH: Final[Path] = REPO_ROOT / "compute_tags.sql"
_DEFAULT_CSV_FILENAME: Final[str] = "syndicated_segments_raw_enriched_data.csv"
CSV_PLACEHOLDER: Final[str] = (
    "'/workspace/csv_dump/' || COALESCE(getenv('CSV_FILENAME'), "
    f"'{_DEFAULT_CSV_FILENAME}')"
)

# Columns consumed by compute_tags.sql (a subset of the 22-column export).
_COLUMNS: Final[tuple[str, ...]] = (
    "dms_segment_id",
    "active_platform_names",
    "active_platforms",
    "active_buyers",
    "cookie_reach",
    "ios_reach",
    "android_reach",
    "is_highly_distributed",
)

# Five rows designed so percentile cutoffs isolate row 1 on reach/buyers.
# p90 of [10, 20, 30, 40, 1000] interpolates to 616; only 1000 clears it.
# p80 interpolates to 232; only 1000 clears all three reach columns.
_FIXTURE_ROWS: Final[tuple[tuple[object, ...], ...]] = (
    (1, "Facebook, MNTN", 5, 100, 1000, 1000, 1000, True),
    (2, "Facebook", 1, 10, 10, 10, 10, False),
    (3, "The Trade Desk", 2, 20, 20, 20, 20, True),
    (4, "Google | Data Marketplace", 1, 30, 30, 30, 30, True),
    (5, "Amazon", 4, 40, 40, 40, 40, False),
)


def _write_fixture_csv(path: Path) -> None:
    """Write the 5-row tab-separated fixture.

    Args:
        path: Destination TSV path.
    """
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, delimiter="\t", quoting=csv.QUOTE_MINIMAL)
        writer.writerow(_COLUMNS)
        writer.writerows(_FIXTURE_ROWS)


def _load_compute_sql(csv_path: Path) -> str:
    """Return ``compute_tags.sql`` with the CSV path expression rewritten to ``csv_path``.

    The SQL uses a ``getenv('CSV_FILENAME')`` expression to locate the file.
    For tests we replace the entire expression with a quoted literal path so
    DuckDB reads the local fixture without needing an environment variable.

    Args:
        csv_path: Absolute path to the fixture TSV.

    Returns:
        SQL ready to execute against an in-memory DuckDB.
    """
    sql = COMPUTE_SQL_PATH.read_text(encoding="utf-8")
    return sql.replace(CSV_PLACEHOLDER, f"'{csv_path}'")


@pytest.fixture
def tagged_conn(tmp_path: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    """Run ``compute_tags.sql`` against the 5-row fixture.

    Args:
        tmp_path: pytest temporary directory.

    Yields:
        An in-memory connection holding the computed tag tables.
    """
    csv_path = tmp_path / _DEFAULT_CSV_FILENAME
    _write_fixture_csv(csv_path)
    connection = duckdb.connect(":memory:")
    connection.execute(_load_compute_sql(csv_path))
    yield connection
    connection.close()


class TestTagDefinitions:
    """The catalogue of computable tags."""

    def test_exactly_eleven_rows(self, tagged_conn: duckdb.DuckDBPyConnection) -> None:
        """Only the 11 tags derivable from ``best_sellers.sql`` are defined."""
        count = tagged_conn.execute("SELECT COUNT(*) FROM tag_definitions").fetchone()
        assert count is not None
        assert count[0] == 11

    def test_keys_are_the_expected_slugs(self, tagged_conn: duckdb.DuckDBPyConnection) -> None:
        """Slugs match the hackathon tag list in priority order."""
        rows = tagged_conn.execute(
            "SELECT tag_key FROM tag_definitions ORDER BY priority"
        ).fetchall()
        assert [row[0] for row in rows] == [
            "top_facebook_activated",
            "top_ttd_activated",
            "top_google_activated",
            "multi_platform_powerhouse",
            "high_ios_reach",
            "high_android_reach",
            "massive_cookie_scale",
            "cross_device_champion",
            "highly_distributed",
            "buyer_magnet",
            "broad_platform_breadth",
        ]


class TestFacebookActivation:
    """``top_facebook_activated`` requires Facebook AND ``is_highly_distributed``."""

    def test_fires_only_when_facebook_and_distributed(
        self,
        tagged_conn: duckdb.DuckDBPyConnection,
    ) -> None:
        """Row 1 (Facebook + distributed) tags; row 2 (Facebook, not distributed) does not."""
        flagged = tagged_conn.execute(
            """
            SELECT segment_id
            FROM tagged
            WHERE top_facebook_activated = true
            ORDER BY segment_id
            """
        ).fetchall()
        assert [row[0] for row in flagged] == [1]

    def test_assignment_table_matches(self, tagged_conn: duckdb.DuckDBPyConnection) -> None:
        """The unpivoted assignment table agrees with the wide ``tagged`` table."""
        assigned = tagged_conn.execute(
            """
            SELECT segment_id
            FROM segment_tag_assignments
            WHERE tag_key = 'top_facebook_activated'
            """
        ).fetchall()
        assert [row[0] for row in assigned] == [1]


class TestCrossDeviceChampion:
    """``cross_device_champion`` requires all three reach columns above p80."""

    def test_requires_all_three_reach_columns(
        self,
        tagged_conn: duckdb.DuckDBPyConnection,
    ) -> None:
        """Only the high-reach row (id 1) clears p80 on cookie, iOS, and Android."""
        flagged = tagged_conn.execute(
            """
            SELECT segment_id
            FROM tagged
            WHERE cross_device_champion = true
            ORDER BY segment_id
            """
        ).fetchall()
        assert [row[0] for row in flagged] == [1]


class TestPlatformAndBreadth:
    """Sanity checks on the remaining platform rules using the same fixture."""

    def test_ttd_and_google_fire_on_matching_rows(
        self,
        tagged_conn: duckdb.DuckDBPyConnection,
    ) -> None:
        """Rows 3 and 4 are distributed on TTD and Google respectively."""
        ttd = tagged_conn.execute(
            "SELECT segment_id FROM tagged WHERE top_ttd_activated = true"
        ).fetchall()
        google = tagged_conn.execute(
            "SELECT segment_id FROM tagged WHERE top_google_activated = true"
        ).fetchall()
        assert [row[0] for row in ttd] == [3]
        assert [row[0] for row in google] == [4]

    def test_multi_platform_requires_five(self, tagged_conn: duckdb.DuckDBPyConnection) -> None:
        """Only row 1 has ``active_platforms >= 5``."""
        rows = tagged_conn.execute(
            "SELECT segment_id FROM tagged WHERE multi_platform_powerhouse = true"
        ).fetchall()
        assert [row[0] for row in rows] == [1]

    def test_broad_breadth_includes_four_and_five(
        self,
        tagged_conn: duckdb.DuckDBPyConnection,
    ) -> None:
        """Rows 1 (5 platforms) and 5 (4 platforms) both qualify."""
        rows = tagged_conn.execute(
            """
            SELECT segment_id FROM tagged
            WHERE broad_platform_breadth = true
            ORDER BY segment_id
            """
        ).fetchall()
        assert [row[0] for row in rows] == [1, 5]


class TestSegmentDump:
    """The dump is persisted as a table so the API can page it."""

    def test_dump_is_a_base_table(self, tagged_conn: duckdb.DuckDBPyConnection) -> None:
        """``segment_dump`` is a TABLE, not a view over the CSV path."""
        row = tagged_conn.execute(
            """
            SELECT table_type FROM information_schema.tables
            WHERE table_name = 'segment_dump'
            """
        ).fetchone()
        assert row is not None
        assert row[0] == "BASE TABLE"

    def test_dump_row_count_matches_fixture(self, tagged_conn: duckdb.DuckDBPyConnection) -> None:
        """Every fixture row is copied into ``segment_dump``."""
        count = tagged_conn.execute("SELECT COUNT(*) FROM segment_dump").fetchone()
        assert count is not None
        assert count[0] == 5

    def test_recompute_when_table_already_exists(self, tmp_path: Path) -> None:
        """A second compute run replaces ``segment_dump`` instead of erroring."""
        csv_path = tmp_path / _DEFAULT_CSV_FILENAME
        _write_fixture_csv(csv_path)
        connection = duckdb.connect(":memory:")
        connection.execute(_load_compute_sql(csv_path))
        connection.execute(_load_compute_sql(csv_path))
        count = connection.execute("SELECT COUNT(*) FROM segment_dump").fetchone()
        connection.close()
        assert count is not None
        assert count[0] == 5

    def test_legacy_raw_object_does_not_block(self, tmp_path: Path) -> None:
        """A leftover ``raw`` VIEW or TABLE must not prevent compute."""
        csv_path = tmp_path / _DEFAULT_CSV_FILENAME
        _write_fixture_csv(csv_path)
        connection = duckdb.connect(":memory:")
        connection.execute(
            f"""
            CREATE VIEW raw AS
            SELECT * FROM read_csv_auto('{csv_path}', header=true)
            """
        )
        connection.execute(_load_compute_sql(csv_path))
        row = connection.execute(
            """
            SELECT table_type FROM information_schema.tables
            WHERE table_name = 'segment_dump'
            """
        ).fetchone()
        connection.close()
        assert row is not None
        assert row[0] == "BASE TABLE"
