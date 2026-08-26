"""Shared fixtures for tag-api unit tests.

An in-memory DuckDB is seeded with 3 tag definitions, 5 segment-tag
assignments, and 5 inverted-index rows so store tests never touch a file.
"""

from __future__ import annotations

from collections.abc import Iterator

import duckdb
import pytest

from models import TagDefinition
from store import DuckDbTagStore

TAG_HIGH_IOS: TagDefinition = TagDefinition(
    tag_key="high_ios_reach",
    display_name="High iOS Reach",
    description="Top 10% by iOS device reach",
    category="reach",
    priority=5,
)
TAG_BUYER_MAGNET: TagDefinition = TagDefinition(
    tag_key="buyer_magnet",
    display_name="Buyer Magnet",
    description="Top 10% by active buyers",
    category="distribution",
    priority=10,
)
TAG_DISTRIBUTED: TagDefinition = TagDefinition(
    tag_key="highly_distributed",
    display_name="Highly Distributed",
    description="Top 10% by destination accounts",
    category="distribution",
    priority=9,
)

SEED_TAGS: tuple[TagDefinition, ...] = (TAG_HIGH_IOS, TAG_DISTRIBUTED, TAG_BUYER_MAGNET)
"""Three definitions in priority order: 5, 9, 10."""

# (segment_id, tag_key, score)
SEED_ASSIGNMENTS: tuple[tuple[int, str, float], ...] = (
    (1001, "high_ios_reach", 1.0),
    (1001, "buyer_magnet", 1.0),
    (1002, "high_ios_reach", 0.9),
    (1003, "highly_distributed", 1.0),
    (1004, "buyer_magnet", 0.8),
)
"""Five assignment rows covering four segments; segment 1005 has none."""

# (dms_segment_id, segment_name, active_platform_names, active_platforms,
#  active_buyers, cookie_reach, ios_reach, android_reach, is_highly_distributed)
SEED_RAW: tuple[tuple[object, ...], ...] = (
    (1001, "Segment 1001", "Facebook", 3, 50, 100, 100, 100, True),
    (1002, "Segment 1002", "The Trade Desk", 2, 20, 80, 80, 80, False),
    (1003, "Segment 1003", "MNTN", 1, 10, 30, 30, 30, True),
    (1004, "Segment 1004", "Amazon", 4, 40, 40, 40, 40, False),
    (1005, "Segment 1005", "Xandr", 1, 1, 1, 1, 1, False),
)
"""Five dump rows; 1005 has no tag assignments."""


def _seed(connection: duckdb.DuckDBPyConnection) -> None:
    """Create the store tables and insert the fixture rows.

    Args:
        connection: Open in-memory DuckDB connection.
    """
    connection.execute(
        """
        CREATE TABLE tag_definitions (
            tag_key VARCHAR PRIMARY KEY,
            display_name VARCHAR,
            description VARCHAR,
            category VARCHAR,
            priority INTEGER
        )
        """
    )
    connection.executemany(
        """
        INSERT INTO tag_definitions
            (tag_key, display_name, description, category, priority)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            (tag.tag_key, tag.display_name, tag.description, tag.category, tag.priority)
            for tag in SEED_TAGS
        ],
    )
    connection.execute(
        """
        CREATE TABLE segment_tag_assignments (
            segment_id BIGINT,
            tag_key VARCHAR,
            score DOUBLE,
            computed_at VARCHAR
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE tag_segment_index (
            tag_key VARCHAR,
            segment_id BIGINT,
            score DOUBLE
        )
        """
    )
    rows = [
        (segment_id, tag_key, score, "2026-08-26T00:00:00")
        for segment_id, tag_key, score in SEED_ASSIGNMENTS
    ]
    connection.executemany(
        """
        INSERT INTO segment_tag_assignments
            (segment_id, tag_key, score, computed_at)
        VALUES (?, ?, ?, ?)
        """,
        rows,
    )
    connection.executemany(
        """
        INSERT INTO tag_segment_index (tag_key, segment_id, score)
        VALUES (?, ?, ?)
        """,
        [(tag_key, segment_id, score) for segment_id, tag_key, score in SEED_ASSIGNMENTS],
    )
    connection.execute(
        """
        CREATE TABLE segment_dump (
            dms_segment_id BIGINT,
            segment_name VARCHAR,
            active_platform_names VARCHAR,
            active_platforms INTEGER,
            active_buyers INTEGER,
            cookie_reach BIGINT,
            ios_reach BIGINT,
            android_reach BIGINT,
            is_highly_distributed BOOLEAN
        )
        """
    )
    connection.executemany(
        """
        INSERT INTO segment_dump (
            dms_segment_id, segment_name, active_platform_names, active_platforms,
            active_buyers, cookie_reach, ios_reach, android_reach, is_highly_distributed
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        list(SEED_RAW),
    )


@pytest.fixture
def conn() -> Iterator[duckdb.DuckDBPyConnection]:
    """Return a seeded in-memory DuckDB connection.

    Yields:
        A connection that is closed after the test.
    """
    connection = duckdb.connect(":memory:")
    _seed(connection)
    yield connection
    connection.close()


@pytest.fixture
def store(conn: duckdb.DuckDBPyConnection) -> DuckDbTagStore:
    """Return a ``DuckDbTagStore`` over the seeded in-memory database.

    Args:
        conn: Seeded DuckDB connection.

    Returns:
        Store bound to ``conn``. Closing the store is the test's responsibility
        only when it opened its own file; the fixture connection is closed by
        ``conn``.
    """
    return DuckDbTagStore(conn)
