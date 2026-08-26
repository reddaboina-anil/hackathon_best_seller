"""Unit tests for ``DuckDbTagStore`` and ``EmptyTagStore``.

Store tests use the in-memory DuckDB fixture from ``conftest``. Missing-file
behaviour is covered with a path under ``tmp_path``.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from exceptions import TagNotFoundError, TagStoreError
from models import TagDefinition
from store import DuckDbTagStore, EmptyTagStore, open_tag_store


class TestGetTagsForSegment:
    """``get_tags_for_segment`` hit and miss paths."""

    def test_hit_returns_tags_ordered_by_priority(self, store: DuckDbTagStore) -> None:
        """Segment 1001 has two tags; lower priority number comes first."""
        tags = store.get_tags_for_segment(1001)
        assert [tag.tag_key for tag in tags] == ["high_ios_reach", "buyer_magnet"]
        assert all(isinstance(tag, TagDefinition) for tag in tags)

    def test_miss_returns_empty_list(self, store: DuckDbTagStore) -> None:
        """A segment with no assignments yields an empty list, not an error."""
        assert store.get_tags_for_segment(1005) == []

    def test_unknown_segment_returns_empty_list(self, store: DuckDbTagStore) -> None:
        """An id that never appears in the store is treated as a miss."""
        assert store.get_tags_for_segment(999999) == []


class TestGetSegmentsForTag:
    """``get_segments_for_tag`` listing and pagination."""

    def test_members_ordered_by_score_desc(self, store: DuckDbTagStore) -> None:
        """high_ios_reach members: 1001 (score 1.0) then 1002 (score 0.9)."""
        page = store.get_segments_for_tag("high_ios_reach", page=1, size=50)
        assert page.tag_key == "high_ios_reach"
        assert page.items == [1001, 1002]
        assert page.pagination.total_items == 2
        assert page.pagination.has_next is False
        assert page.pagination.has_previous is False

    def test_pagination_slices_results(self, store: DuckDbTagStore) -> None:
        """Page size 1 yields two pages for a two-member tag."""
        first = store.get_segments_for_tag("high_ios_reach", page=1, size=1)
        second = store.get_segments_for_tag("high_ios_reach", page=2, size=1)
        assert first.items == [1001]
        assert first.pagination.has_next is True
        assert first.pagination.total_pages == 2
        assert second.items == [1002]
        assert second.pagination.has_previous is True
        assert second.pagination.has_next is False

    def test_page_past_end_is_empty(self, store: DuckDbTagStore) -> None:
        """A page beyond the last still reports the true total."""
        page = store.get_segments_for_tag("high_ios_reach", page=9, size=10)
        assert page.items == []
        assert page.pagination.total_items == 2

    def test_unknown_slug_raises(self, store: DuckDbTagStore) -> None:
        """A slug missing from tag_definitions is a not-found error."""
        with pytest.raises(TagNotFoundError, match="not_a_real_tag"):
            store.get_segments_for_tag("not_a_real_tag", page=1, size=10)


class TestListSegments:
    """``list_segments`` pagination and tag attachment."""

    def test_ordered_by_segment_id(self, store: DuckDbTagStore) -> None:
        """Rows come back in ``dms_segment_id`` order with tags attached."""
        page = store.list_segments(page=1, size=50)
        assert [row.dms_segment_id for row in page.items] == [1001, 1002, 1003, 1004, 1005]
        assert page.pagination.total_items == 5
        assert [tag.tag_key for tag in page.items[0].tags] == ["high_ios_reach", "buyer_magnet"]
        assert page.items[4].tags == []

    def test_pagination_slices_results(self, store: DuckDbTagStore) -> None:
        """Page size 2 yields three pages for five rows."""
        first = store.list_segments(page=1, size=2)
        last = store.list_segments(page=3, size=2)
        assert [row.dms_segment_id for row in first.items] == [1001, 1002]
        assert first.pagination.has_next is True
        assert [row.dms_segment_id for row in last.items] == [1005]
        assert last.pagination.has_previous is True
        assert last.pagination.has_next is False

    def test_platform_names_are_split(self, store: DuckDbTagStore) -> None:
        """The dump's joined platform string becomes a list."""
        page = store.list_segments(page=1, size=1)
        assert page.items[0].active_platform_names == ["Facebook"]


class TestListTags:
    """``list_tags`` ordering."""

    def test_ordered_by_priority(self, store: DuckDbTagStore) -> None:
        """Definitions are returned in ascending priority."""
        keys = [tag.tag_key for tag in store.list_tags()]
        assert keys == ["high_ios_reach", "highly_distributed", "buyer_magnet"]

    def test_count_matches_seed(self, store: DuckDbTagStore) -> None:
        """Exactly three definitions are seeded."""
        assert len(store.list_tags()) == 3


class TestEmptyTagStore:
    """Null Object used when the DuckDB file has not been computed."""

    def test_list_tags_is_empty(self) -> None:
        """No definitions when the store is empty."""
        assert EmptyTagStore().list_tags() == []

    def test_get_tags_for_segment_is_empty(self) -> None:
        """No assignments when the store is empty."""
        assert EmptyTagStore().get_tags_for_segment(1001) == []

    def test_get_segments_for_tag_is_empty_page(self) -> None:
        """Unknown slugs degrade to an empty page instead of crashing."""
        page = EmptyTagStore().get_segments_for_tag("high_ios_reach", page=1, size=50)
        assert page.items == []
        assert page.pagination.total_items == 0

    def test_list_segments_is_empty_page(self) -> None:
        """Dump browse degrades to an empty page."""
        page = EmptyTagStore().list_segments(page=1, size=50)
        assert page.items == []
        assert page.pagination.total_items == 0

    def test_close_is_noop(self) -> None:
        """Closing the null store does not raise."""
        EmptyTagStore().close()


class TestOpenTagStore:
    """``open_tag_store`` missing-file graceful degradation."""

    def test_missing_file_returns_empty_store(self, tmp_path: Path) -> None:
        """A path that does not exist yields ``EmptyTagStore``."""
        store = open_tag_store(tmp_path / "tags.duckdb")
        assert isinstance(store, EmptyTagStore)
        assert store.list_tags() == []

    def test_directory_is_treated_as_missing(self, tmp_path: Path) -> None:
        """A directory at the expected path is not a DuckDB file."""
        store = open_tag_store(tmp_path)
        assert isinstance(store, EmptyTagStore)

    def test_existing_file_opens_read_only(self, tmp_path: Path) -> None:
        """A real DuckDB file is opened as ``DuckDbTagStore``."""
        db_path = tmp_path / "tags.duckdb"
        seed = duckdb.connect(str(db_path))
        seed.execute(
            """
            CREATE TABLE tag_definitions (
                tag_key VARCHAR, display_name VARCHAR, description VARCHAR,
                category VARCHAR, priority INTEGER
            )
            """
        )
        seed.execute(
            """
            INSERT INTO tag_definitions VALUES
            ('high_ios_reach', 'High iOS Reach', 'Top 10%', 'reach', 5)
            """
        )
        seed.execute("CREATE TABLE segment_dump (dms_segment_id BIGINT)")
        seed.execute("INSERT INTO segment_dump VALUES (1001)")
        seed.close()
        store = open_tag_store(db_path)
        assert isinstance(store, DuckDbTagStore)
        assert [tag.tag_key for tag in store.list_tags()] == ["high_ios_reach"]
        store.close()

    def test_incomplete_file_returns_empty_store(self, tmp_path: Path) -> None:
        """A DuckDB file without ``tag_definitions`` degrades to the null store."""
        db_path = tmp_path / "tags.duckdb"
        seed = duckdb.connect(str(db_path))
        seed.execute("CREATE TABLE leftover (id INTEGER)")
        seed.close()
        store = open_tag_store(db_path)
        assert isinstance(store, EmptyTagStore)
        assert store.list_tags() == []

    def test_definitions_without_dump_returns_empty_store(self, tmp_path: Path) -> None:
        """A store that has tags but no persisted dump degrades to empty."""
        db_path = tmp_path / "tags.duckdb"
        seed = duckdb.connect(str(db_path))
        seed.execute(
            """
            CREATE TABLE tag_definitions (
                tag_key VARCHAR, display_name VARCHAR, description VARCHAR,
                category VARCHAR, priority INTEGER
            )
            """
        )
        seed.close()
        store = open_tag_store(db_path)
        assert isinstance(store, EmptyTagStore)

    def test_corrupt_file_raises(self, tmp_path: Path) -> None:
        """A non-DuckDB file at the expected path is a store error."""
        db_path = tmp_path / "tags.duckdb"
        db_path.write_text("not a duckdb file", encoding="utf-8")
        with pytest.raises(TagStoreError, match="Failed to open tag store"):
            open_tag_store(db_path)


class TestStoreQueryFailures:
    """DuckDB errors surface as ``TagStoreError``."""

    def test_list_tags_after_close(self, store: DuckDbTagStore) -> None:
        """Querying a closed connection is a store error."""
        store.close()
        with pytest.raises(TagStoreError, match="Failed to query tag definitions"):
            store.list_tags()

    def test_get_tags_for_segment_after_close(self, store: DuckDbTagStore) -> None:
        """Segment lookup on a closed connection is a store error."""
        store.close()
        with pytest.raises(TagStoreError, match="Failed to query tag definitions"):
            store.get_tags_for_segment(1001)

    def test_get_segments_for_tag_after_close(self, store: DuckDbTagStore) -> None:
        """Inverted-index lookup on a closed connection is a store error."""
        store.close()
        with pytest.raises(TagStoreError, match="Failed to list segments"):
            store.get_segments_for_tag("high_ios_reach", page=1, size=10)

    def test_list_segments_after_close(self, store: DuckDbTagStore) -> None:
        """Dump browse on a closed connection is a store error."""
        store.close()
        with pytest.raises(TagStoreError, match="Failed to list segments"):
            store.list_segments(page=1, size=10)
