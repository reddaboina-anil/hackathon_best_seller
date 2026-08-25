"""FastAPI dependency providers.

Both providers are cached for the process lifetime: settings are immutable and
the CSV catalog is parsed once. Tests override them through
``app.dependency_overrides``.
"""

from __future__ import annotations

from functools import lru_cache

from lr_bestsellers.config import Settings
from lr_bestsellers.service import load_settings
from lr_bestsellers.store.csv_catalog import CsvCatalogRepository


@lru_cache(maxsize=1)
def get_api_settings() -> Settings:
    """Return the settings instance shared by all requests.

    Returns:
        Validated settings, or offline-safe placeholders when the environment is
        incomplete.
    """
    return load_settings()


@lru_cache(maxsize=1)
def get_catalog_repository() -> CsvCatalogRepository:
    """Return the process-wide CSV catalog repository.

    The repository is constructed eagerly but parses its dump lazily, so an
    unreadable file surfaces on first request rather than at import time.

    Returns:
        Repository reading ``Settings.csv_catalog_path``.
    """
    return CsvCatalogRepository(get_api_settings().csv_catalog_path)
