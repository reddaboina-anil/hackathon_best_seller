"""FastAPI dependency providers for the tag API.

``get_tag_store`` is a bounded process singleton. The DuckDB connection is
opened once (in lifespan via this provider) and reused for every request.
Tests replace it through ``app.dependency_overrides``.
"""

from __future__ import annotations

from functools import lru_cache

from config import get_settings
from store import TagStoreProtocol, open_tag_store


@lru_cache(maxsize=1)
def get_tag_store() -> TagStoreProtocol:
    """Return the process-wide tag store.

    Opens ``Settings.tags_duckdb_path`` read-only. When the file is missing
    an ``EmptyTagStore`` is returned so the API starts without tags.

    Returns:
        A ``TagStoreProtocol`` implementation.
    """
    return open_tag_store(get_settings().tags_duckdb_path)
