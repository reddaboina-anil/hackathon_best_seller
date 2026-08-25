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

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_LLM_MODEL: Final[str] = "gemini-2.0-flash"
"""Default Gemini chat model used when ``LLM_MODEL`` is unset."""

DEFAULT_EMBEDDING_MODEL: Final[str] = "gemini-embedding-2"
"""Default Gemini embedding model used when ``EMBEDDING_MODEL`` is unset."""

DEFAULT_CSV_CATALOG_PATH: Final[Path] = Path("csv_dump/segment_recommendation_features.csv")
"""Default location of the BigQuery CSV dump used when ``CSV_CATALOG_PATH`` is unset."""


class Settings(BaseSettings):
    """Validated application configuration loaded from environment variables.

    Fields without defaults are **required** — a ``ValidationError`` is raised
    at startup if they are absent from the environment or ``.env`` file.

    Attributes:
        google_api_key: Gemini API key for the chat and embedding models.
        llm_model: Gemini chat model id (default ``gemini-2.0-flash``).
        embedding_model: Gemini embedding model id (default ``gemini-embedding-2``).
        bigquery_project: GCP project ID that owns the BigQuery dataset.
        csv_catalog_path: Path to the offline BigQuery CSV dump.
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

    # ── CSV catalog (offline BigQuery dump) ──────────────────────────────────
    csv_catalog_path: Path = Field(
        DEFAULT_CSV_CATALOG_PATH,
        validation_alias=AliasChoices(
            "csv_catalog_path",
            "CSV_CATALOG_PATH",
            "CSV_DUMP_PATH",
        ),
        description=(
            "Path to the segment recommendation features CSV dump served by the "
            "browse branch of the segments API."
        ),
    )

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
