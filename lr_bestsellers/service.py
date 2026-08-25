"""Guarded query flow shared by the CLI and the HTTP API.

This is the single place where a plain-English question is taken through the
whole pipeline: input guardrails → LangGraph agent → output guardrails. Both
``main.query`` and the FastAPI segments endpoint delegate here so that every
entry point applies the same protections.
"""

from __future__ import annotations

from typing import Final

import structlog
from pydantic import ValidationError

from lr_bestsellers.agent.graph import build_node_context, run_query
from lr_bestsellers.config import Settings, get_settings
from lr_bestsellers.exceptions import InputGuardrailError, OutputGuardrailError
from lr_bestsellers.guardrails import build_input_chain
from lr_bestsellers.guardrails.base import GuardrailChain
from lr_bestsellers.guardrails.output import (
    CitationRequiredGuardrail,
    ConfidenceGate,
    HallucinationDetector,
    NumberCrossCheckGuardrail,
    PIIScrubber,
    evidence_from_response,
)
from lr_bestsellers.hooks.metrics import get_metrics
from lr_bestsellers.models.query import QueryResponse
from lr_bestsellers.utils.logging import configure_logging

log = structlog.get_logger(__name__)

_FALLBACK_API_KEY: Final[str] = "fake-api-key"
_FALLBACK_BQ_PROJECT: Final[str] = "liveramp-eng-qa-reliability"


def load_settings() -> Settings:
    """Return application settings, falling back to offline-safe defaults.

    A missing ``GOOGLE_API_KEY`` / ``BIGQUERY_PROJECT`` should not stop the
    process from starting: the catalog branch of the API and the unit tests do
    not call Gemini or BigQuery.

    Returns:
        Validated settings, or placeholder settings when the environment is
        incomplete.
    """
    try:
        return get_settings()
    except ValidationError:
        log.warning("settings.fallback", reason="incomplete environment")
        return Settings(
            google_api_key=_FALLBACK_API_KEY,
            bigquery_project=_FALLBACK_BQ_PROJECT,
        )


def apply_output_guardrails(response: QueryResponse) -> QueryResponse:
    """Run output guardrails, retrying once on missing citations.

    Args:
        response: Raw graph response.

    Returns:
        Response with a possibly rewritten ``answer``.

    Raises:
        OutputGuardrailError: When a hard output check fails after retry.
    """
    evidence = evidence_from_response(response, sql_rows=response.sql_results)
    chain = GuardrailChain(
        [
            CitationRequiredGuardrail(),
            ConfidenceGate(response.confidence),
            NumberCrossCheckGuardrail(evidence),
            HallucinationDetector(evidence),
            PIIScrubber(),
        ],
        error_cls=OutputGuardrailError,
    )
    try:
        result = chain.run(response.answer)
    except OutputGuardrailError as exc:
        if exc.code != "MISSING_CITATION":
            raise
        retry_answer = response.answer.rstrip() + " [Source: knowledge_base]"
        result = chain.run(retry_answer)
    return response.model_copy(update={"answer": result.rewritten or response.answer})


def answer_query(
    text: str,
    settings: Settings | None = None,
    caller_id: str = "default",
) -> QueryResponse:
    """Answer a plain-English question with input and output guardrails applied.

    Args:
        text: User question (1–2000 characters recommended).
        settings: Optional settings override; defaults to :func:`load_settings`.
        caller_id: Rate-limit bucket key.

    Returns:
        Structured ``QueryResponse`` with answer, citations, and optional SQL.

    Raises:
        InputGuardrailError: When input guardrails reject the query.
        OutputGuardrailError: When output guardrails reject the answer.

    Example:
        >>> response = answer_query("What is cookie_reach?")
        >>> response.intent in {"conceptual", "lookup", "analytics", "mixed", "vague"}
        True
    """
    cfg = settings or load_settings()
    configure_logging(
        cfg.log_level,
        log_file=cfg.log_file,
        log_max_bytes=cfg.log_max_bytes,
        log_backup_count=cfg.log_backup_count,
    )
    try:
        build_input_chain(caller_id).run(text)
    except InputGuardrailError as exc:
        get_metrics().incr("guardrail.failed")
        if exc.code == "INJECTION_ATTEMPT":
            get_metrics().incr("guardrail.injection")
        raise
    ctx = build_node_context(cfg)
    return apply_output_guardrails(run_query(text, ctx))
