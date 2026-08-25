"""CLI and ``query()`` entry point for lr-bestsellers.

The guarded pipeline itself lives in :mod:`lr_bestsellers.service` so that the
CLI and the HTTP API share one implementation.
"""

from __future__ import annotations

import sys

from lr_bestsellers.config import Settings
from lr_bestsellers.exceptions import InputGuardrailError, OutputGuardrailError
from lr_bestsellers.models.query import QueryResponse
from lr_bestsellers.service import answer_query, apply_output_guardrails

__all__ = ["apply_output_guardrails", "main", "query"]


def query(
    text: str,
    settings: Settings | None = None,
    caller_id: str = "default",
) -> QueryResponse:
    """Ask a plain-English question and receive a grounded, cited answer.

    Args:
        text: User question (1–2000 characters recommended).
        settings: Optional settings override; defaults to loaded settings.
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
    return answer_query(text, settings, caller_id)


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
