"""Custom exception hierarchy for lr-bestsellers.

All domain exceptions derive from :class:`BestSellersError`, which lets
callers catch the full hierarchy with a single ``except BestSellersError``
clause while still being able to handle specific sub-types.

Hierarchy::

    BestSellersError
    ├── RetrievalError
    ├── EmbeddingError
    ├── SQLGenerationError
    ├── IngestionError
    ├── ThresholdNotMetError
    └── GuardrailError
        ├── InputGuardrailError
        ├── SQLGuardrailError
        └── OutputGuardrailError
"""

from __future__ import annotations


class BestSellersError(Exception):
    """Base exception for all lr-bestsellers runtime errors.

    Catching this class intercepts every domain-specific exception raised by
    the system, which is useful at top-level boundaries (e.g. the FastAPI
    exception handler).
    """


class RetrievalError(BestSellersError):
    """Raised when a vector store retrieval operation fails.

    Typical causes: Qdrant is unreachable, the collection does not exist, or
    an unexpected response shape is returned.

    Example:
        >>> raise RetrievalError("Qdrant search failed for collection 'domain_knowledge'")
    """


class EmbeddingError(BestSellersError):
    """Raised when an embedding generation call to the Google API fails.

    Typical causes: invalid API key, quota exceeded, or a malformed payload.

    Example:
        >>> raise EmbeddingError("text-embedding-004 request timed out")
    """


class SQLGenerationError(BestSellersError):
    """Raised when Text2SQL generation produces invalid or unusable SQL.

    Typical causes: Gemini returns unparseable output or the guardrail rejects
    the generated statement.

    Example:
        >>> raise SQLGenerationError("LLM returned non-SELECT statement")
    """


class IngestionError(BestSellersError):
    """Raised when data ingestion into Qdrant fails.

    Typical causes: upsert rejected due to dimension mismatch, network error,
    or malformed chunk payload.

    Example:
        >>> raise IngestionError("Failed to upsert batch of 50 chunks into 'segment_catalog'")
    """


class ThresholdNotMetError(BestSellersError):
    """Raised when no retrieved chunks meet the configured similarity threshold.

    The agent converts this into a grounded fallback response rather than
    propagating it to the caller.

    Example:
        >>> raise ThresholdNotMetError("Best score 0.42 < threshold 0.65")
    """


class GuardrailError(BestSellersError):
    """Base exception for guardrail violations.

    Every guardrail attaches a machine-readable ``code`` so downstream
    handlers (e.g. metrics counters, HTTP error mappers) can branch without
    string-matching the message.

    Attributes:
        code: Machine-readable violation identifier (e.g. ``PII_DETECTED``).

    Example:
        >>> raise GuardrailError("PII found in query", code="PII_DETECTED")
    """

    def __init__(self, message: str, *, code: str) -> None:
        """Initialise with a human-readable message and a machine-readable code.

        Args:
            message: Human-readable description of the violation.
            code: Short uppercase identifier for the violation type.
        """
        super().__init__(message)
        self.code = code


class InputGuardrailError(GuardrailError):
    """Raised when an input guardrail rejects the user's query.

    This is raised before any LLM call is made, so no tokens are consumed.

    Example:
        >>> raise InputGuardrailError("Query too short", code="QUERY_TOO_SHORT")
    """


class SQLGuardrailError(GuardrailError):
    """Raised when an SQL guardrail rejects LLM-generated SQL.

    Typical codes: ``SQL_NOT_SELECT``, ``DISALLOWED_TABLE``,
    ``QUERY_TOO_EXPENSIVE``.

    Example:
        >>> raise SQLGuardrailError("DELETE statement detected", code="SQL_NOT_SELECT")
    """


class OutputGuardrailError(GuardrailError):
    """Raised when an output guardrail rejects the agent's synthesised response.

    Typical codes: ``MISSING_CITATION``, ``ANSWER_NUMBER_MISMATCH``,
    ``HALLUCINATION_RISK``.

    Example:
        >>> raise OutputGuardrailError("Answer contains uncited claim", code="MISSING_CITATION")
    """
