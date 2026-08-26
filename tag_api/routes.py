"""HTTP routes for the standalone tag API."""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Query

from dependencies import get_tag_store
from models import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    ErrorResponse,
    SegmentsPage,
    TagDefinition,
    TagsPage,
)
from store import TagStoreProtocol

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/v1", tags=["tags"])

_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    404: {"model": ErrorResponse, "description": "Tag slug is not defined."},
    503: {"model": ErrorResponse, "description": "Tag store is unreadable."},
}


@router.get(
    "/tags",
    response_model=list[TagDefinition],
    summary="List all tag definitions",
    response_description="Every computable tag, ordered by priority.",
    responses=_ERROR_RESPONSES,
)
def list_tags(
    store: Annotated[TagStoreProtocol, Depends(get_tag_store)],
) -> list[TagDefinition]:
    """Return every tag definition.

    Args:
        store: Injected tag store.

    Returns:
        Tag definitions in priority order.

    Raises:
        TagStoreError: When the store cannot be queried.
    """
    log.info("api.tags.list")
    return store.list_tags()


@router.get(
    "/segments",
    response_model=SegmentsPage,
    summary="Page every segment with its dump columns and tags",
    response_description="A page of dump rows, each with assigned tag definitions.",
    responses=_ERROR_RESPONSES,
)
def list_segments(
    store: Annotated[TagStoreProtocol, Depends(get_tag_store)],
    page: Annotated[int, Query(ge=1, description="1-based page number.")] = 1,
    size: Annotated[
        int,
        Query(ge=1, le=MAX_PAGE_SIZE, description="Rows per page."),
    ] = DEFAULT_PAGE_SIZE,
) -> SegmentsPage:
    """Return one page of ``segment_dump`` rows with tags attached.

    Args:
        store: Injected tag store.
        page: 1-based page number.
        size: Page size.

    Returns:
        Paginated dump rows in ``dms_segment_id`` order.

    Raises:
        TagStoreError: When the store cannot be queried.
    """
    log.info("api.segments.list", page=page, size=size)
    return store.list_segments(page, size)


@router.get(
    "/segments/{segment_id}/tags",
    response_model=list[TagDefinition],
    summary="List tags for a segment",
    response_description="Tags assigned to the given segment, ordered by priority.",
    responses=_ERROR_RESPONSES,
)
def get_segment_tags(
    segment_id: int,
    store: Annotated[TagStoreProtocol, Depends(get_tag_store)],
) -> list[TagDefinition]:
    """Return every tag assigned to ``segment_id``.

    Args:
        segment_id: LiveRamp ``dms_segment_id``.
        store: Injected tag store.

    Returns:
        Assigned tag definitions; empty when the segment has none.

    Raises:
        TagStoreError: When the store cannot be queried.
    """
    log.info("api.tags.for_segment", segment_id=segment_id)
    return store.get_tags_for_segment(segment_id)


@router.get(
    "/tags/{slug}/segments",
    response_model=TagsPage,
    summary="List segments that carry a tag",
    response_description="Paginated segment IDs for the given tag slug.",
    responses=_ERROR_RESPONSES,
)
def get_tag_segments(
    slug: str,
    store: Annotated[TagStoreProtocol, Depends(get_tag_store)],
    page: Annotated[int, Query(ge=1, description="1-based page number.")] = 1,
    size: Annotated[
        int,
        Query(ge=1, le=MAX_PAGE_SIZE, description="Segment IDs per page."),
    ] = DEFAULT_PAGE_SIZE,
) -> TagsPage:
    """Return a page of segment IDs that carry ``slug``.

    Args:
        slug: Tag key, e.g. ``high_ios_reach``.
        store: Injected tag store.
        page: 1-based page number.
        size: Page size.

    Returns:
        Paginated segment IDs ordered by score descending.

    Raises:
        TagNotFoundError: When ``slug`` is not a known tag.
        TagStoreError: When the store cannot be queried.
    """
    log.info("api.tags.segments", slug=slug, page=page, size=size)
    return store.get_segments_for_tag(slug, page, size)
