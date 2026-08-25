"""Guardrail protocol, result DTO, and sequential chain."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import structlog
from pydantic import BaseModel

from lr_bestsellers.exceptions import GuardrailError

log = structlog.get_logger(__name__)


class GuardrailResult(BaseModel):
    """Outcome of a single guardrail check.

    Attributes:
        passed: Whether the value is allowed (possibly after rewrite).
        code: Machine-readable violation code when ``passed`` is False, or a
            warning code when the check logged a risk but did not block.
        message: Human-readable detail.
        rewritten: Replacement payload (SQL or answer text) when auto-fixed.
    """

    passed: bool
    code: str | None = None
    message: str = ""
    rewritten: str | None = None


@runtime_checkable
class Guardrail(Protocol):
    """A single validation step over a string payload."""

    @property
    def name(self) -> str:
        """Stable guardrail name for logs and metrics."""
        ...

    def check(self, value: str) -> GuardrailResult:
        """Evaluate ``value``.

        Args:
            value: Query text, SQL, or answer text.

        Returns:
            Pass/fail result, optionally with a rewritten string.
        """
        ...


class GuardrailChain:
    """Run guardrails in order; stop on the first hard failure.

    Args:
        guardrails: Ordered checks.
        error_cls: Exception type raised on failure.
    """

    def __init__(
        self,
        guardrails: list[Guardrail],
        error_cls: type[GuardrailError] = GuardrailError,
    ) -> None:
        """Store the chain and exception class.

        Args:
            guardrails: Ordered checks.
            error_cls: Error type for failures.
        """
        self._guardrails = guardrails
        self._error_cls = error_cls

    def run(self, value: str) -> GuardrailResult:
        """Apply each guardrail, threading rewrites forward.

        Args:
            value: Original payload.

        Returns:
            The last successful result (``rewritten`` holds the final payload).

        Raises:
            GuardrailError: Subclass supplied at init when a check fails.
        """
        current = value
        last = GuardrailResult(passed=True, rewritten=current)
        for item in self._guardrails:
            result = item.check(current)
            log.info(
                "guardrail.check",
                guardrail=item.name,
                passed=result.passed,
                code=result.code,
            )
            if not result.passed:
                raise self._error_cls(
                    result.message or f"{item.name} failed",
                    code=result.code or "GUARDRAIL_FAILED",
                )
            if result.rewritten is not None:
                current = result.rewritten
            last = result.model_copy(update={"rewritten": current, "passed": True})
        return last
