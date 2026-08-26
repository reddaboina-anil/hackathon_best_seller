"""Domain exceptions for the standalone tag API.

Hierarchy::

    TagStoreError
    TagNotFoundError
"""

from __future__ import annotations


class TagStoreError(Exception):
    """Raised when the DuckDB tag store cannot be opened or queried.

    Typical causes: the file is corrupt, a query fails, or the connection
    is closed.

    Example:
        >>> raise TagStoreError("Failed to open tag store at /app/duckdb_data/tags.duckdb")
    """


class TagNotFoundError(Exception):
    """Raised when a requested tag slug is not in ``tag_definitions``.

    Example:
        >>> raise TagNotFoundError("Unknown tag 'not_a_real_tag'")
    """
