"""Read-only repositories over the pre-computed DuckDB tag store.

``TagStoreProtocol`` is the injection boundary. ``DuckDbTagStore`` owns all
SQL. ``EmptyTagStore`` is the Null Object used when ``tags.duckdb`` has not
been computed yet, so the API process starts cleanly without tags.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from pathlib import Path
from typing import Protocol, cast, runtime_checkable

import duckdb
import structlog
from pydantic import ValidationError

from exceptions import TagNotFoundError, TagStoreError
from models import (
    PageInfo,
    SegmentRow,
    SegmentsPage,
    TagCategory,
    TagDefinition,
    TagsPage,
)

log = structlog.get_logger(__name__)


@runtime_checkable
class TagStoreProtocol(Protocol):
    """Read-only access to tag definitions and segment assignments.

    Implementations must be interchangeable. The protocol has four methods so
    callers never depend on DuckDB-specific behaviour.
    """

    def get_tags_for_segment(self, segment_id: int) -> list[TagDefinition]:
        """Return every tag assigned to ``segment_id``, ordered by priority.

        Args:
            segment_id: LiveRamp ``dms_segment_id``.

        Returns:
            Tag definitions; empty when the segment has no tags.
        """
        ...

    def get_segments_for_tag(self, slug: str, page: int, size: int) -> TagsPage:
        """Return a page of segment IDs that carry ``slug``.

        Args:
            slug: Tag key, e.g. ``high_ios_reach``.
            page: 1-based page number.
            size: Page size.

        Returns:
            Paginated segment IDs ordered by score descending.

        Raises:
            TagNotFoundError: When ``slug`` is not a known tag.
        """
        ...

    def list_segments(self, page: int, size: int) -> SegmentsPage:
        """Return one page of dump rows plus tags.

        Args:
            page: 1-based page number.
            size: Page size.

        Returns:
            Paginated dump rows in ``dms_segment_id`` order.
        """
        ...

    def list_tags(self) -> list[TagDefinition]:
        """Return every tag definition, ordered by priority.

        Returns:
            All tag definitions in display order.
        """
        ...


class EmptyTagStore:
    """Null Object: every read returns an empty collection.

    Used when ``tags.duckdb`` does not exist yet so the API can boot before
    the first ``tag-compute`` run.
    """

    def get_tags_for_segment(self, segment_id: int) -> list[TagDefinition]:
        """Return no tags for any segment.

        Args:
            segment_id: LiveRamp ``dms_segment_id`` (ignored).

        Returns:
            An empty list.
        """
        del segment_id
        return []

    def get_segments_for_tag(self, slug: str, page: int, size: int) -> TagsPage:
        """Return an empty page for any slug without raising.

        Args:
            slug: Tag key (recorded on the page, not validated).
            page: 1-based page number.
            size: Page size.

        Returns:
            An empty ``TagsPage``.
        """
        return TagsPage(
            tag_key=slug,
            pagination=PageInfo.from_total(page, size, 0),
            items=[],
        )

    def list_segments(self, page: int, size: int) -> SegmentsPage:
        """Return an empty page of dump rows.

        Args:
            page: 1-based page number.
            size: Page size.

        Returns:
            An empty ``SegmentsPage``.
        """
        return SegmentsPage(
            pagination=PageInfo.from_total(page, size, 0),
            items=[],
        )

    def list_tags(self) -> list[TagDefinition]:
        """Return no tag definitions.

        Returns:
            An empty list.
        """
        return []

    def close(self) -> None:
        """No-op; there is no underlying connection."""


class DuckDbTagStore:
    """Repository over a read-only DuckDB connection.

    One connection is opened in the FastAPI lifespan and shared for the
    process lifetime. Queries are serialised with a lock because FastAPI
    runs synchronous endpoints in a thread pool.

    Args:
        connection: An open DuckDB connection (typically ``read_only=True``).
    """

    def __init__(self, connection: duckdb.DuckDBPyConnection) -> None:
        """Store the connection without issuing queries.

        Args:
            connection: Open DuckDB connection owned by this instance.
        """
        self._conn = connection
        self._lock = threading.Lock()

    def close(self) -> None:
        """Close the underlying DuckDB connection."""
        self._conn.close()

    def get_tags_for_segment(self, segment_id: int) -> list[TagDefinition]:
        """Return every tag assigned to ``segment_id``, ordered by priority.

        Args:
            segment_id: LiveRamp ``dms_segment_id``.

        Returns:
            Tag definitions; empty when the segment has no tags.

        Raises:
            TagStoreError: When the query fails.
        """
        sql = """
            SELECT td.tag_key, td.display_name, td.description, td.category, td.priority
            FROM segment_tag_assignments sta
            JOIN tag_definitions td ON td.tag_key = sta.tag_key
            WHERE sta.segment_id = ?
            ORDER BY td.priority
        """
        return self._fetch_definitions(sql, [segment_id])

    def get_segments_for_tag(self, slug: str, page: int, size: int) -> TagsPage:
        """Return a page of segment IDs that carry ``slug``.

        Args:
            slug: Tag key, e.g. ``high_ios_reach``.
            page: 1-based page number.
            size: Page size.

        Returns:
            Paginated segment IDs ordered by score descending then segment id.

        Raises:
            TagNotFoundError: When ``slug`` is not in ``tag_definitions``.
            TagStoreError: When a query fails.
        """
        offset = (page - 1) * size
        try:
            with self._lock:
                exists = self._conn.execute(
                    "SELECT 1 FROM tag_definitions WHERE tag_key = ?",
                    [slug],
                ).fetchone()
                if exists is None:
                    raise TagNotFoundError(f"Unknown tag '{slug}'")
                total_row = self._conn.execute(
                    "SELECT COUNT(*) FROM tag_segment_index WHERE tag_key = ?",
                    [slug],
                ).fetchone()
                total = int(total_row[0]) if total_row is not None else 0
                rows = self._conn.execute(
                    """
                    SELECT segment_id
                    FROM tag_segment_index
                    WHERE tag_key = ?
                    ORDER BY score DESC, segment_id
                    LIMIT ? OFFSET ?
                    """,
                    [slug, size, offset],
                ).fetchall()
        except TagNotFoundError:
            raise
        except duckdb.Error as exc:
            log.error("tag_store.query_failed", method="get_segments_for_tag", error=str(exc))
            raise TagStoreError(f"Failed to list segments for tag '{slug}'") from exc
        return TagsPage(
            tag_key=slug,
            pagination=PageInfo.from_total(page, size, total),
            items=[int(row[0]) for row in rows],
        )

    def list_segments(self, page: int, size: int) -> SegmentsPage:
        """Return one page of dump rows plus tags.

        Args:
            page: 1-based page number.
            size: Page size.

        Returns:
            Paginated dump rows in ``dms_segment_id`` order.

        Raises:
            TagStoreError: When a query fails or a row cannot be mapped.
        """
        offset = (page - 1) * size
        try:
            with self._lock:
                total_row = self._conn.execute("SELECT COUNT(*) FROM segment_dump").fetchone()
                total_items = int(total_row[0]) if total_row is not None else 0
                result = self._conn.execute(
                    """
                    SELECT * FROM segment_dump
                    ORDER BY dms_segment_id
                    LIMIT ? OFFSET ?
                    """,
                    [size, offset],
                )
                columns = [desc[0] for desc in result.description] if result.description else []
                if "dms_segment_id" not in columns:
                    raise TagStoreError("segment_dump has no dms_segment_id column")
                raw_rows = result.fetchall()
                items = self._map_dump_rows_locked(columns, raw_rows)
        except TagStoreError:
            raise
        except duckdb.Error as exc:
            log.error("tag_store.query_failed", method="list_segments", error=str(exc))
            raise TagStoreError("Failed to list segments") from exc
        return SegmentsPage(
            pagination=PageInfo.from_total(page, size, total_items),
            items=items,
        )

    def list_tags(self) -> list[TagDefinition]:
        """Return every tag definition, ordered by priority.

        Returns:
            All tag definitions in display order.

        Raises:
            TagStoreError: When the query fails.
        """
        sql = """
            SELECT tag_key, display_name, description, category, priority
            FROM tag_definitions
            ORDER BY priority
        """
        return self._fetch_definitions(sql, [])

    def _fetch_definitions(self, sql: str, params: list[object]) -> list[TagDefinition]:
        """Run ``sql`` and map each row to a ``TagDefinition``.

        Args:
            sql: Parameterised SELECT returning the five definition columns.
            params: Bound parameters for ``sql``.

        Returns:
            Mapped tag definitions.

        Raises:
            TagStoreError: When DuckDB raises.
        """
        try:
            with self._lock:
                rows = self._conn.execute(sql, params).fetchall()
        except duckdb.Error as exc:
            log.error("tag_store.query_failed", error=str(exc))
            raise TagStoreError("Failed to query tag definitions") from exc
        return [
            TagDefinition(
                tag_key=str(row[0]),
                display_name=str(row[1]),
                description=str(row[2]),
                category=cast(TagCategory, row[3]),
                priority=int(row[4]),
            )
            for row in rows
        ]

    def _map_dump_rows_locked(
        self,
        columns: list[str],
        raw_rows: list[tuple[object, ...]],
    ) -> list[SegmentRow]:
        """Map dump rows and attach tags. Caller must hold ``self._lock``.

        Args:
            columns: Column names from the dump SELECT.
            raw_rows: Result tuples in the same column order.

        Returns:
            Validated dump rows with tags.

        Raises:
            TagStoreError: When a row cannot be mapped or the tag query fails.
        """
        id_index = columns.index("dms_segment_id")
        segment_ids = [int(str(row[id_index])) for row in raw_rows if row]
        tags_by_id: dict[int, list[TagDefinition]] = defaultdict(list)
        if segment_ids:
            placeholders = ", ".join("?" * len(segment_ids))
            try:
                tag_rows = self._conn.execute(
                    f"""
                    SELECT sta.segment_id, td.tag_key, td.display_name,
                           td.description, td.category, td.priority
                    FROM segment_tag_assignments sta
                    JOIN tag_definitions td ON td.tag_key = sta.tag_key
                    WHERE sta.segment_id IN ({placeholders})
                    ORDER BY sta.segment_id, td.priority
                    """,
                    list(segment_ids),
                ).fetchall()
            except duckdb.Error as exc:
                log.error("tag_store.query_failed", method="list_segments.tags", error=str(exc))
                raise TagStoreError("Failed to load tags for dump rows") from exc
            for tag_row in tag_rows:
                tags_by_id[int(tag_row[0])].append(
                    TagDefinition(
                        tag_key=str(tag_row[1]),
                        display_name=str(tag_row[2]),
                        description=str(tag_row[3]),
                        category=cast(TagCategory, tag_row[4]),
                        priority=int(tag_row[5]),
                    )
                )
        try:
            return [
                SegmentRow.model_validate(
                    {**dict(zip(columns, row, strict=True)), "tags": tags_by_id[segment_id]}
                )
                for row, segment_id in zip(raw_rows, segment_ids, strict=True)
            ]
        except (ValidationError, ValueError, TypeError) as exc:
            log.error("tag_store.row_invalid", error=str(exc))
            raise TagStoreError("Invalid dump row in tag store") from exc


def open_tag_store(path: Path) -> TagStoreProtocol:
    """Open a DuckDB tag store, or return ``EmptyTagStore`` when the file is missing.

    Args:
        path: Path to ``tags.duckdb``.

    Returns:
        A ``DuckDbTagStore`` when the file contains ``tag_definitions`` and
        ``segment_dump`` as base tables, otherwise ``EmptyTagStore``.

    Raises:
        TagStoreError: When the file exists but cannot be opened.

    Example:
        >>> isinstance(open_tag_store(Path("does-not-exist.duckdb")), EmptyTagStore)
        True
    """
    if not path.is_file():
        log.warning("tag_store.missing", path=str(path))
        return EmptyTagStore()
    try:
        connection = duckdb.connect(str(path), read_only=True)
        ready = connection.execute(
            """
            SELECT COUNT(*) FROM information_schema.tables
            WHERE table_type = 'BASE TABLE'
              AND table_name IN ('tag_definitions', 'segment_dump')
            """
        ).fetchone()
        if ready is None or int(ready[0]) < 2:
            log.warning("tag_store.incomplete", path=str(path))
            connection.close()
            return EmptyTagStore()
    except duckdb.Error as exc:
        log.error("tag_store.open_failed", path=str(path), error=str(exc))
        raise TagStoreError(f"Failed to open tag store at {path}") from exc
    log.info("tag_store.opened", path=str(path))
    return DuckDbTagStore(connection)
