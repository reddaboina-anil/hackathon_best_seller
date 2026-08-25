"""Unit tests for Settings(BaseSettings) in lr_bestsellers.config.

These tests verify validation rules and default values without touching the
filesystem or environment.  Required fields are always supplied directly to
the Settings constructor, which overrides env-file loading.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from lr_bestsellers.config import Settings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REQUIRED = {
    "google_api_key": "fake-api-key",
    "bigquery_project": "my-gcp-project",
}


def make_settings(**overrides: object) -> Settings:
    """Construct a Settings instance with safe test defaults.

    Merges ``_REQUIRED`` with ``overrides`` so individual tests only need to
    supply the field(s) under test.

    Args:
        **overrides: Field values to override on top of the required defaults.

    Returns:
        A validated ``Settings`` instance.
    """
    return Settings(**{**_REQUIRED, **overrides})  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Required fields
# ---------------------------------------------------------------------------


class TestRequiredFields:
    """Tests that required fields without defaults are enforced."""

    def test_missing_google_api_key(self) -> None:
        """Omitting google_api_key raises ValidationError."""
        with pytest.raises(ValidationError):
            Settings(bigquery_project="proj")  # type: ignore[call-arg]

    def test_missing_bigquery_project(self) -> None:
        """Omitting bigquery_project raises ValidationError."""
        with pytest.raises(ValidationError):
            Settings(google_api_key="key")  # type: ignore[call-arg]

    def test_all_required_present(self) -> None:
        """Settings constructed successfully when all required fields provided."""
        settings = make_settings()
        assert settings.bigquery_project == "my-gcp-project"


# ---------------------------------------------------------------------------
# Default values
# ---------------------------------------------------------------------------


class TestDefaults:
    """Tests that optional fields have their documented default values."""

    def test_qdrant_url_default(self) -> None:
        """qdrant_url defaults to local Docker address."""
        assert make_settings().qdrant_url == "http://localhost:6333"

    def test_qdrant_api_key_default_none(self) -> None:
        """qdrant_api_key defaults to None for local instances."""
        assert make_settings().qdrant_api_key is None

    def test_langsmith_api_key_default_none(self) -> None:
        """langsmith_api_key defaults to None (tracing is opt-in)."""
        assert make_settings().langsmith_api_key is None

    def test_langsmith_tracing_default_false(self) -> None:
        """LangSmith tracing is disabled by default."""
        assert make_settings().langsmith_tracing_v2 is False

    def test_similarity_threshold_default(self) -> None:
        """similarity_threshold defaults to 0.65."""
        assert make_settings().similarity_threshold == 0.65

    def test_max_retrieval_results_default(self) -> None:
        """max_retrieval_results defaults to 10."""
        assert make_settings().max_retrieval_results == 10

    def test_log_level_default(self) -> None:
        """log_level defaults to 'INFO'."""
        assert make_settings().log_level == "INFO"

    def test_environment_default(self) -> None:
        """Environment defaults to 'development'."""
        assert make_settings().environment == "development"

    def test_langsmith_project_default(self) -> None:
        """langsmith_project defaults to 'lr-bestsellers'."""
        assert make_settings().langsmith_project == "lr-bestsellers"


# ---------------------------------------------------------------------------
# Overrides / custom values
# ---------------------------------------------------------------------------


class TestCustomValues:
    """Tests that fields accept valid non-default values."""

    def test_custom_qdrant_url(self) -> None:
        """Custom Qdrant Cloud URL accepted."""
        settings = make_settings(qdrant_url="https://my-cluster.qdrant.io")
        assert settings.qdrant_url == "https://my-cluster.qdrant.io"

    def test_custom_similarity_threshold(self) -> None:
        """Similarity threshold can be set to any value in [0, 1]."""
        settings = make_settings(similarity_threshold=0.80)
        assert settings.similarity_threshold == 0.80

    def test_environment_staging(self) -> None:
        """environment='staging' is a valid Literal value."""
        settings = make_settings(environment="staging")
        assert settings.environment == "staging"

    def test_environment_production(self) -> None:
        """environment='production' is a valid Literal value."""
        settings = make_settings(environment="production")
        assert settings.environment == "production"

    def test_log_level_debug(self) -> None:
        """log_level='DEBUG' is accepted."""
        settings = make_settings(log_level="DEBUG")
        assert settings.log_level == "DEBUG"


# ---------------------------------------------------------------------------
# Validation failures
# ---------------------------------------------------------------------------


class TestValidationFailures:
    """Tests for invalid field values that must be rejected."""

    def test_similarity_threshold_above_one(self) -> None:
        """similarity_threshold > 1.0 raises ValidationError."""
        with pytest.raises(ValidationError):
            make_settings(similarity_threshold=1.5)

    def test_similarity_threshold_below_zero(self) -> None:
        """similarity_threshold < 0.0 raises ValidationError."""
        with pytest.raises(ValidationError):
            make_settings(similarity_threshold=-0.1)

    def test_max_retrieval_results_zero(self) -> None:
        """max_retrieval_results=0 raises ValidationError (ge=1)."""
        with pytest.raises(ValidationError):
            make_settings(max_retrieval_results=0)

    def test_max_retrieval_results_over_limit(self) -> None:
        """max_retrieval_results=101 raises ValidationError (le=100)."""
        with pytest.raises(ValidationError):
            make_settings(max_retrieval_results=101)

    def test_invalid_log_level(self) -> None:
        """Unknown log_level string raises ValidationError."""
        with pytest.raises(ValidationError):
            make_settings(log_level="VERBOSE")  # type: ignore[arg-type]

    def test_invalid_environment(self) -> None:
        """Unknown environment string raises ValidationError."""
        with pytest.raises(ValidationError):
            make_settings(environment="local")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# SecretStr handling
# ---------------------------------------------------------------------------


class TestSecretFields:
    """Tests that sensitive fields are wrapped in SecretStr."""

    def test_google_api_key_is_secret(self) -> None:
        """google_api_key is stored as SecretStr and not exposed in repr."""
        settings = make_settings()
        assert "fake-api-key" not in repr(settings)
        assert settings.google_api_key.get_secret_value() == "fake-api-key"

    def test_qdrant_api_key_is_secret_when_set(self) -> None:
        """qdrant_api_key is SecretStr when provided."""
        settings = make_settings(qdrant_api_key="qdrant-secret")
        assert settings.qdrant_api_key is not None
        assert settings.qdrant_api_key.get_secret_value() == "qdrant-secret"
