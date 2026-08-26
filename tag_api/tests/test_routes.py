"""HTTP tests for the tag-api endpoints.

The store is replaced with a ``MagicMock`` via ``dependency_overrides`` so
these tests never open DuckDB.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from dependencies import get_tag_store
from exceptions import TagNotFoundError, TagStoreError
from main import create_app
from models import PageInfo, SegmentRow, SegmentsPage, TagDefinition, TagsPage

HIGH_IOS = TagDefinition(
    tag_key="high_ios_reach",
    display_name="High iOS Reach",
    description="Top 10% by iOS device reach",
    category="reach",
    priority=5,
)
BUYER_MAGNET = TagDefinition(
    tag_key="buyer_magnet",
    display_name="Buyer Magnet",
    description="Top 10% by active buyers",
    category="distribution",
    priority=10,
)


@pytest.fixture
def mock_store() -> MagicMock:
    """Return a store mock with happy-path defaults.

    Returns:
        A ``MagicMock`` that satisfies ``TagStoreProtocol``.
    """
    store = MagicMock()
    store.list_tags.return_value = [HIGH_IOS, BUYER_MAGNET]
    store.get_tags_for_segment.return_value = [HIGH_IOS]
    store.get_segments_for_tag.return_value = TagsPage(
        tag_key="high_ios_reach",
        pagination=PageInfo.from_total(page=1, page_size=50, total_items=2),
        items=[1001, 1002],
    )
    store.list_segments.return_value = SegmentsPage(
        pagination=PageInfo.from_total(page=1, page_size=50, total_items=2),
        items=[
            SegmentRow(
                dms_segment_id=1001,
                segment_name="Segment 1001",
                tags=[HIGH_IOS],
            )
        ],
    )
    return store


@pytest.fixture
def client(mock_store: MagicMock) -> Iterator[TestClient]:
    """Return a test client whose store dependency is the mock.

    Args:
        mock_store: Store stand-in.

    Yields:
        A ``TestClient`` bound to a fresh app.
    """
    application = create_app()
    application.dependency_overrides[get_tag_store] = lambda: mock_store
    with TestClient(application) as test_client:
        yield test_client


class TestListTags:
    """``GET /v1/tags``."""

    def test_happy_path(self, client: TestClient, mock_store: MagicMock) -> None:
        """Returns the mocked definitions."""
        response = client.get("/v1/tags")
        assert response.status_code == 200
        body = response.json()
        assert [row["tag_key"] for row in body] == ["high_ios_reach", "buyer_magnet"]
        mock_store.list_tags.assert_called_once_with()

    def test_store_failure_is_503(self, client: TestClient, mock_store: MagicMock) -> None:
        """A store error maps to 503 with a stable code."""
        mock_store.list_tags.side_effect = TagStoreError("corrupt")
        response = client.get("/v1/tags")
        assert response.status_code == 503
        assert response.json() == {
            "error": "TAG_STORE_UNAVAILABLE",
            "detail": "corrupt",
        }


class TestGetSegmentTags:
    """``GET /v1/segments/{segment_id}/tags``."""

    def test_happy_path(self, client: TestClient, mock_store: MagicMock) -> None:
        """Returns tags for the requested segment."""
        response = client.get("/v1/segments/1001/tags")
        assert response.status_code == 200
        assert response.json()[0]["tag_key"] == "high_ios_reach"
        mock_store.get_tags_for_segment.assert_called_once_with(1001)

    def test_empty_when_no_tags(self, client: TestClient, mock_store: MagicMock) -> None:
        """A segment with no tags is 200 and an empty list."""
        mock_store.get_tags_for_segment.return_value = []
        response = client.get("/v1/segments/1005/tags")
        assert response.status_code == 200
        assert response.json() == []

    def test_store_failure_is_503(self, client: TestClient, mock_store: MagicMock) -> None:
        """A store error maps to 503."""
        mock_store.get_tags_for_segment.side_effect = TagStoreError("closed")
        response = client.get("/v1/segments/1001/tags")
        assert response.status_code == 503
        assert response.json()["error"] == "TAG_STORE_UNAVAILABLE"


class TestListSegments:
    """``GET /v1/segments``."""

    def test_happy_path(self, client: TestClient, mock_store: MagicMock) -> None:
        """Returns a page of dump rows with tags."""
        response = client.get("/v1/segments", params={"page": 1, "size": 50})
        assert response.status_code == 200
        body = response.json()
        assert body["items"][0]["dms_segment_id"] == 1001
        assert body["items"][0]["tags"][0]["tag_key"] == "high_ios_reach"
        mock_store.list_segments.assert_called_once_with(1, 50)

    def test_store_failure_is_503(self, client: TestClient, mock_store: MagicMock) -> None:
        """A store error maps to 503."""
        mock_store.list_segments.side_effect = TagStoreError("io")
        response = client.get("/v1/segments")
        assert response.status_code == 503
        assert response.json()["error"] == "TAG_STORE_UNAVAILABLE"

    def test_size_over_limit(self, client: TestClient) -> None:
        """Size above the maximum is rejected by validation."""
        assert client.get("/v1/segments", params={"size": 500}).status_code == 422

    def test_page_zero(self, client: TestClient) -> None:
        """Page must be 1-based."""
        assert client.get("/v1/segments", params={"page": 0}).status_code == 422


class TestGetTagSegments:
    """``GET /v1/tags/{slug}/segments``."""

    def test_happy_path(self, client: TestClient, mock_store: MagicMock) -> None:
        """Returns a page of segment IDs."""
        response = client.get("/v1/tags/high_ios_reach/segments", params={"page": 1, "size": 50})
        assert response.status_code == 200
        body = response.json()
        assert body["tag_key"] == "high_ios_reach"
        assert body["items"] == [1001, 1002]
        assert body["pagination"]["total_items"] == 2
        mock_store.get_segments_for_tag.assert_called_once_with("high_ios_reach", 1, 50)

    def test_unknown_slug_is_404(self, client: TestClient, mock_store: MagicMock) -> None:
        """An unknown tag slug maps to 404 with a stable code."""
        mock_store.get_segments_for_tag.side_effect = TagNotFoundError(
            "Unknown tag 'not_a_real_tag'"
        )
        response = client.get("/v1/tags/not_a_real_tag/segments")
        assert response.status_code == 404
        assert response.json() == {
            "error": "TAG_NOT_FOUND",
            "detail": "Unknown tag 'not_a_real_tag'",
        }

    def test_store_failure_is_503(self, client: TestClient, mock_store: MagicMock) -> None:
        """A store error maps to 503."""
        mock_store.get_segments_for_tag.side_effect = TagStoreError("io")
        response = client.get("/v1/tags/high_ios_reach/segments")
        assert response.status_code == 503
        assert response.json()["error"] == "TAG_STORE_UNAVAILABLE"

    def test_size_over_limit(self, client: TestClient) -> None:
        """Size above the maximum is rejected by validation."""
        assert (
            client.get("/v1/tags/high_ios_reach/segments", params={"size": 500}).status_code == 422
        )

    def test_page_zero(self, client: TestClient) -> None:
        """Page must be 1-based."""
        assert client.get("/v1/tags/high_ios_reach/segments", params={"page": 0}).status_code == 422


class TestHealthz:
    """``GET /healthz`` liveness probe."""

    def test_ok(self, client: TestClient) -> None:
        """Returns 200 without touching the tag store."""
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


class TestOpenApiSchema:
    """The generated OpenAPI document describes the endpoints."""

    def test_paths_are_documented(self, client: TestClient) -> None:
        """Tag routes and the liveness probe appear in the schema."""
        paths = client.get("/openapi.json").json()["paths"]
        assert "/healthz" in paths
        assert "/v1/tags" in paths
        assert "/v1/segments" in paths
        assert "/v1/segments/{segment_id}/tags" in paths
        assert "/v1/tags/{slug}/segments" in paths

    def test_docs_available(self, client: TestClient) -> None:
        """Swagger UI is served for interactive exploration."""
        assert client.get("/docs").status_code == 200
