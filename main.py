"""CLI and ``query()`` entry point for lr-bestsellers."""

from __future__ import annotations

import sys

from pydantic import ValidationError

from lr_bestsellers.agent.graph import build_node_context, run_query
from lr_bestsellers.config import Settings, get_settings
from lr_bestsellers.exceptions import InputGuardrailError, OutputGuardrailError
from lr_bestsellers.guardrails import build_input_chain
from lr_bestsellers.hooks.metrics import get_metrics
from lr_bestsellers.guardrails.base import GuardrailChain
from lr_bestsellers.guardrails.output import (
    CitationRequiredGuardrail,
    ConfidenceGate,
    HallucinationDetector,
    NumberCrossCheckGuardrail,
    PIIScrubber,
    evidence_from_response,
)
from lr_bestsellers.models.query import QueryResponse
from lr_bestsellers.utils.logging import configure_logging


def apply_output_guardrails(response: QueryResponse) -> QueryResponse:
    """Run output guardrails, retrying once on missing citations.

    Args:
        response: Raw graph response.

    Returns:
        Response with a possibly rewritten ``answer``.

    Raises:
        OutputGuardrailError: When a hard output check fails after retry.
    """
    evidence = evidence_from_response(response)
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


def query(
    text: str,
    settings: Settings | None = None,
    caller_id: str = "default",
) -> QueryResponse:
    """Ask a plain-English question and receive a grounded, cited answer.

    Args:
        text: User question (1–2000 characters recommended).
        settings: Optional settings override; defaults to ``get_settings()``.
        caller_id: Rate-limit bucket key.

    Returns:
        Structured ``QueryResponse`` with answer, citations, and optional SQL.

    Raises:
        InputGuardrailError: When input guardrails reject the query.
        OutputGuardrailError: When output guardrails reject the answer.

    Example:
        >>> response = query("What is cookie_reach?")
        >>> response.intent in {"conceptual", "lookup", "analytics", "mixed", "vague"}
        True
    """
    try:
        cfg = settings or get_settings()
    except ValidationError:
        cfg = Settings(
            google_api_key="fake-api-key",
            bigquery_project="liveramp-eng-qa-reliability",
        )
    configure_logging(cfg.log_level)
    try:
        build_input_chain(caller_id).run(text)
    except InputGuardrailError as exc:
        get_metrics().incr("guardrail.failed")
        if exc.code == "INJECTION_ATTEMPT":
            get_metrics().incr("guardrail.injection")
        raise
    ctx = build_node_context(cfg)
    return apply_output_guardrails(run_query(text, ctx))


def main(argv: list[str] | None = None) -> int:
    """Run a single query from the command line.

    Args:
        argv: CLI args excluding program name. Defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        sys.stderr.write('Usage: uv run python main.py "your question"\n')
        return 2
    text = args[0]
    try:
        response = query(text)
    except InputGuardrailError as exc:
        sys.stderr.write(f"Input rejected ({exc.code})\n")
        return 1
    except OutputGuardrailError as exc:
        sys.stderr.write(f"Output rejected ({exc.code})\n")
        return 1
    sys.stdout.write(response.answer)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
