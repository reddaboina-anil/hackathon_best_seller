"""Application-wide configuration via pydantic-settings.

All runtime settings are loaded once from environment variables (and an
optional ``.env`` file) into a single ``Settings`` instance. Callers should
obtain settings through dependency injection; only ``main.py`` constructs the
object directly.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Final, Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_LLM_MODEL: Final[str] = "gemini-3.6-flash"
"""Default Gemini chat model used when ``LLM_MODEL`` is unset."""

DEFAULT_EMBEDDING_MODEL: Final[str] = "gemini-embedding-2"
"""Default Gemini embedding model used when ``EMBEDDING_MODEL`` is unset."""

DEFAULT_CSV_FILENAME: Final[str] = "syndicated_segments_raw_enriched_data.csv"
"""Default enriched CSV filename used when ``CSV_FILENAME`` is unset."""

DEFAULT_CSV_CATALOG_PATH: Final[Path] = Path("csv_dump") / DEFAULT_CSV_FILENAME
"""Default location of the enriched CSV dump used when ``CSV_CATALOG_PATH`` is unset."""

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
"""Repository root; relative data paths are resolved against this directory."""


def resolve_data_path(value: Path) -> Path:
    """Resolve a relative data path against the repository root.

    Uvicorn's working directory is not always the repo root (IDE run configs,
    ``--reload`` children). Relative paths like ``csv_dump/<filename>``
    must not depend on ``Path.cwd()``.

    Args:
        value: Configured filesystem path.

    Returns:
        An absolute path. Absolute inputs are returned unchanged.
    """
    expanded = value.expanduser()
    if expanded.is_absolute():
        return expanded
    return (_REPO_ROOT / expanded).resolve()


class Settings(BaseSettings):
    """Validated application configuration loaded from environment variables.

    Fields without defaults are **required** — a ``ValidationError`` is raised
    at startup if they are absent from the environment or ``.env`` file.

    Attributes:
        google_api_key: Gemini API key for the chat and embedding models.
        llm_model: Gemini chat model id (default ``gemini-3.6-flash``).
        embedding_model: Gemini embedding model id (default ``gemini-embedding-2``).
        bigquery_project: GCP project ID that owns the BigQuery dataset.
        csv_filename: Filename of the enriched CSV export (single source of truth).
        csv_catalog_path: Path to the enriched CSV served by the browse API.
        catalog_ingest_csv: Path to the enriched CSV used by Qdrant ingest.
        qdrant_url: Qdrant server URL (default ``http://localhost:6333``).
        qdrant_api_key: Qdrant Cloud API key; ``None`` for local instances.
        langsmith_api_key: LangSmith tracing API key (optional).
        langsmith_project: LangSmith project name.
        langsmith_tracing_v2: Enable LangSmith V2 tracing.
        similarity_threshold: Minimum cosine similarity for retrieval (0–1).
        max_retrieval_results: Maximum retrieved chunks before re-ranking.
        log_level: Python log level string.
        environment: Deployment environment label.

    Example:
        >>> settings = Settings()  # reads from environment / .env
        >>> settings.bigquery_project
        'my-gcp-project'
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    # ── Google / Gemini ──────────────────────────────────────────────────────
    google_api_key: SecretStr = Field(
        ...,
        validation_alias=AliasChoices("google_api_key", "GOOGLE_API_KEY", "GEMINI_API_KEY"),
        description="Gemini API key for the configured chat and embedding models.",
    )
    llm_model: str = Field(
        DEFAULT_LLM_MODEL,
        min_length=1,
        validation_alias=AliasChoices("llm_model", "LLM_MODEL", "GEMINI_MODEL"),
        description="Gemini chat model id.",
    )
    embedding_model: str = Field(
        DEFAULT_EMBEDDING_MODEL,
        min_length=1,
        validation_alias=AliasChoices("embedding_model", "EMBEDDING_MODEL"),
        description="Gemini embedding model id.",
    )

    # ── BigQuery ─────────────────────────────────────────────────────────────
    # bigquery_project is the BILLING project (job quota/cost), not the data
    # project. Tables are fully qualified in best_sellers.sql
    # (e.g. `liveramp-eng-pie.entities.*`).
    bigquery_project: str = Field(
        ...,
        validation_alias=AliasChoices(
            "bigquery_project",
            "BIGQUERY_PROJECT",
            "BQ_PROJECT",
        ),
        description=(
            "GCP billing project for BigQuery jobs (e.g. liveramp-eng-qa-reliability). "
            "Data tables are fully qualified in best_sellers.sql."
        ),
    )
    google_application_credentials: str | None = Field(
        None,
        description=(
            "Path to a GCP service-account JSON key. When None, the BigQuery "
            "client falls back to Application Default Credentials (ADC)."
        ),
    )

    # ── Enriched CSV (single source for all consumers) ───────────────────────
    csv_filename: str = Field(
        DEFAULT_CSV_FILENAME,
        min_length=1,
        validation_alias=AliasChoices("csv_filename", "CSV_FILENAME"),
        description=(
            "Filename of the enriched BigQuery CSV export placed under "
            "``csv_dump/``. Change this one value to switch every consumer "
            "(browse API, Qdrant ingest, DuckDB tag-compute) to a new export. "
            f"Default: ``{DEFAULT_CSV_FILENAME}``."
        ),
    )
    csv_catalog_path: Path = Field(
        DEFAULT_CSV_CATALOG_PATH,
        validation_alias=AliasChoices(
            "csv_catalog_path",
            "CSV_CATALOG_PATH",
            "CSV_DUMP_PATH",
        ),
        description=(
            "Full path to the enriched CSV dump served by the browse branch of "
            "GET /v1/segments. Defaults to ``csv_dump/{csv_filename}``. "
            "Set this to override the path entirely; otherwise leave unset and "
            "control the filename via ``CSV_FILENAME``. "
            "Relative paths are resolved against the repository root."
        ),
    )
    catalog_ingest_csv: Path = Field(
        DEFAULT_CSV_CATALOG_PATH,
        validation_alias=AliasChoices("catalog_ingest_csv", "CATALOG_INGEST_CSV"),
        description=(
            "Path to the enriched CSV used by ``--source csv`` Qdrant ingest. "
            "Defaults to the same file as ``csv_catalog_path``. "
            "Override only when the ingest source differs from the browse catalog. "
            "Relative paths are resolved against the repository root."
        ),
    )

    @field_validator("csv_catalog_path", "catalog_ingest_csv", mode="after")
    @classmethod
    def _resolve_csv_paths(cls, value: Path) -> Path:
        """Make CSV paths absolute against the repository root.

        Args:
            value: Raw path from the environment or the field default.

        Returns:
            An absolute path.
        """
        return resolve_data_path(value)

    # ── Qdrant ───────────────────────────────────────────────────────────────
    qdrant_url: str = Field(
        "http://localhost:6333",
        description="Qdrant server base URL.",
    )
    qdrant_api_key: SecretStr | None = Field(
        None,
        description="Qdrant Cloud API key; leave None for local instances.",
    )

    # ── LangSmith (optional tracing) ─────────────────────────────────────────
    langsmith_api_key: SecretStr | None = Field(
        None,
        description="LangSmith API key for distributed tracing.",
    )
    langsmith_project: str = Field(
        "lr-bestsellers",
        description="LangSmith project name shown in the UI.",
    )
    langsmith_tracing_v2: bool = Field(
        False,
        description="Enable LangSmith V2 tracing.",
    )

    # ── Retrieval Tuning ─────────────────────────────────────────────────────
    similarity_threshold: float = Field(
        0.65,
        ge=0.0,
        le=1.0,
        description="Minimum cosine similarity for a chunk to pass the threshold gate.",
    )
    max_retrieval_results: int = Field(
        10,
        ge=1,
        le=100,
        description="Maximum number of candidate chunks to retrieve before re-ranking.",
    )
    top_k_final: int = Field(
        3,
        ge=1,
        le=50,
        description="Number of reranked results sent to the LLM.",
    )

    # ── System ───────────────────────────────────────────────────────────────
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        "INFO",
        description="Application log verbosity.",
    )
    log_file: str | None = Field(
        None,
        description=(
            "Path to a rotating JSON log file. When set, log output is written "
            "to both stdout and this file. Example: 'logs/lr_bestsellers.log'. "
            "The parent directory must exist."
        ),
    )
    log_max_bytes: int = Field(
        10 * 1024 * 1024,
        ge=1,
        description="Maximum size of a single log file in bytes before rotation (default 10 MiB).",
    )
    log_backup_count: int = Field(
        5,
        ge=0,
        description="Number of rotated log files to retain alongside the active file.",
    )
    environment: Literal["development", "staging", "production"] = Field(
        "development",
        description="Deployment environment label.",
    )

    @property
    def bq_project(self) -> str:
        """Return the BigQuery billing project ID.

        Returns:
            The GCP project used to run (and pay for) BigQuery jobs.
        """
        return self.bigquery_project

    @property
    def top_k_retrieval(self) -> int:
        """Return the candidate count used before reranking.

        Returns:
            The same value as ``max_retrieval_results``.
        """
        return self.max_retrieval_results


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a process-wide cached ``Settings`` singleton.

    Returns:
        The validated ``Settings`` instance loaded from the environment.

    Example:
        >>> settings = get_settings()
        >>> settings.qdrant_url
        'http://localhost:6333'
    """
    return Settings()
