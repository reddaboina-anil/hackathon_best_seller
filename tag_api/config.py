"""Application configuration for the standalone tag API.

Settings are loaded from environment variables. The only required runtime
knob is the path to the DuckDB file produced by ``compute_tags.sql``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Final

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_TAGS_DUCKDB_PATH: Final[Path] = Path("duckdb_data/tags.duckdb")
"""Default location of the computed tag store when ``TAGS_DUCKDB_PATH`` is unset."""


class Settings(BaseSettings):
    """Validated tag-api configuration loaded from environment variables.

    Attributes:
        tags_duckdb_path: Path to the read-only DuckDB file of pre-computed tags.

    Example:
        >>> Settings(tags_duckdb_path=Path("/app/duckdb_data/tags.duckdb"))
        Settings(tags_duckdb_path=PosixPath('/app/duckdb_data/tags.duckdb'))
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    tags_duckdb_path: Path = Field(
        DEFAULT_TAGS_DUCKDB_PATH,
        validation_alias=AliasChoices("tags_duckdb_path", "TAGS_DUCKDB_PATH"),
        description="Path to the DuckDB file written by tag-compute.",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a process-wide cached ``Settings`` singleton.

    Returns:
        The validated ``Settings`` instance loaded from the environment.

    Example:
        >>> get_settings().tags_duckdb_path.name
        'tags.duckdb'
    """
    return Settings()
