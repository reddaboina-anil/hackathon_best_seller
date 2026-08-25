"""Output guardrails: citations, confidence, numbers, hallucination, PII scrub."""

from __future__ import annotations

import re
from typing import Final, Protocol, runtime_checkable

import structlog

from lr_bestsellers.agent.prompts import GROUNDING_FALLBACK
from lr_bestsellers.guardrails.base import GuardrailResult
from lr_bestsellers.models.query import QueryResponse, SqlRow

log = structlog.get_logger(__name__)

_SOURCE_RE: Final[re.Pattern[str]] = re.compile(r"\[Source:\s*[^\]]+\]")
_NUMBER_RE: Final[re.Pattern[str]] = re.compile(r"\b\d{2,}\b")
_EMAIL_RE: Final[re.Pattern[str]] = re.compile(
    r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b"
)
_PHONE_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}\b"
)
_SSN_RE: Final[re.Pattern[str]] = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_SKIP_NUMBERS: Final[frozenset[str]] = frozenset({"10", "100", "1000", "2024", "2025", "2026"})


@runtime_checkable
class FaithfulnessScorerProtocol(Protocol):
    """Scores whether an answer is faithful to evidence."""

    def score(self, answer: str, evidence: str) -> float:
        """Return a faithfulness score in ``[0, 1]``.

        Args:
            answer: Model answer.
            evidence: Retrieved context plus SQL.

        Returns:
            Score in ``[0, 1]``.
        """
        ...


class LexicalFaithfulnessScorer:
    """Overlap-based scorer used when a second Gemini call is unavailable."""

    def score(self, answer: str, evidence: str) -> float:
        """Fraction of answer tokens that appear in evidence.

        Args:
            answer: Model answer.
            evidence: Grounding text.

        Returns:
            Overlap ratio, or ``1.0`` when the answer is the grounded fallback.
        """
        if GROUNDING_FALLBACK[:40] in answer:
            return 1.0
        answer_tokens = {tok for tok in re.findall(r"[a-z0-9]+", answer.lower()) if len(tok) > 2}
        evidence_tokens = set(re.findall(r"[a-z0-9]+", evidence.lower()))
        if not answer_tokens:
            return 1.0
        return len(answer_tokens & evidence_tokens) / len(answer_tokens)


class CitationRequiredGuardrail:
    """Require at least one ``[Source: ...]`` marker unless this is the fallback."""

    @property
    def name(self) -> str:
        """Return ``citation_required``."""
        return "citation_required"

    def check(self, value: str) -> GuardrailResult:
        """Look for citation markers.

        Args:
            value: Answer text.

        Returns:
            Fail with ``MISSING_CITATION``.
        """
        if GROUNDING_FALLBACK[:40] in value:
            return GuardrailResult(passed=True, rewritten=value)
        if _SOURCE_RE.search(value):
            return GuardrailResult(passed=True, rewritten=value)
        return GuardrailResult(
            passed=False,
            code="MISSING_CITATION",
            message="Answer is missing [Source: ...] citations",
        )


class ConfidenceGate:
    """Replace low-confidence answers with the grounded fallback.

    Args:
        confidence: Score from the agent.
        minimum: Gate threshold (default 0.65).
    """

    def __init__(self, confidence: float, minimum: float = 0.65) -> None:
        """Store confidence values.

        Args:
            confidence: Agent confidence.
            minimum: Inclusive minimum to pass.
        """
        self._confidence = confidence
        self._minimum = minimum

    @property
    def name(self) -> str:
        """Return ``confidence_gate``."""
        return "confidence_gate"

    def check(self, value: str) -> GuardrailResult:
        """Pass through or rewrite to the fallback.

        Args:
            value: Answer text.

        Returns:
            Always ``passed=True``; may rewrite to the fallback.
        """
        if self._confidence >= self._minimum:
            return GuardrailResult(passed=True, rewritten=value)
        return GuardrailResult(
            passed=True,
            rewritten=GROUNDING_FALLBACK,
            code="LOW_CONFIDENCE",
            message="Confidence below gate; returning grounded fallback",
        )


class NumberCrossCheckGuardrail:
    """Ensure multi-digit numbers in the answer appear in evidence.

    Args:
        evidence: Concatenated retrieval + SQL text.
    """

    def __init__(self, evidence: str) -> None:
        """Store evidence text.

        Args:
            evidence: Grounding corpus for number checks.
        """
        self._evidence = evidence

    @property
    def name(self) -> str:
        """Return ``number_cross_check``."""
        return "number_cross_check"

    def check(self, value: str) -> GuardrailResult:
        """Fail when an answer number is missing from evidence.

        Args:
            value: Answer text.

        Returns:
            Fail with ``ANSWER_NUMBER_MISMATCH``.
        """
        if GROUNDING_FALLBACK[:40] in value:
            return GuardrailResult(passed=True, rewritten=value)
        evidence = self._evidence
        for match in _NUMBER_RE.findall(value):
            if match in _SKIP_NUMBERS:
                continue
            if match not in evidence:
                return GuardrailResult(
                    passed=False,
                    code="ANSWER_NUMBER_MISMATCH",
                    message="Answer contains a number not present in evidence",
                )
        return GuardrailResult(passed=True, rewritten=value)


class HallucinationDetector:
    """Log ``HALLUCINATION_RISK`` and append a disclaimer when score < 0.80.

    Args:
        evidence: Grounding text.
        scorer: Optional faithfulness scorer.
        minimum: Score floor.
    """

    def __init__(
        self,
        evidence: str,
        scorer: FaithfulnessScorerProtocol | None = None,
        minimum: float = 0.80,
    ) -> None:
        """Store scorer and evidence.

        Args:
            evidence: Grounding text.
            scorer: Faithfulness implementation.
            minimum: Minimum score before a disclaimer is added.
        """
        self._evidence = evidence
        self._scorer = scorer or LexicalFaithfulnessScorer()
        self._minimum = minimum

    @property
    def name(self) -> str:
        """Return ``hallucination_detector``."""
        return "hallucination_detector"

    def check(self, value: str) -> GuardrailResult:
        """Score faithfulness; never hard-fail.

        Args:
            value: Answer text.

        Returns:
            Always passes; may append a disclaimer and set ``HALLUCINATION_RISK``.
        """
        score = self._scorer.score(value, self._evidence)
        if score >= self._minimum:
            return GuardrailResult(passed=True, rewritten=value)
        log.error("HALLUCINATION_RISK", score=score)
        disclaimer = (
            "\n\nDisclaimer: parts of this answer may not be fully supported by "
            "retrieved evidence. [Source: system]"
        )
        rewritten = value if disclaimer.strip() in value else value + disclaimer
        return GuardrailResult(
            passed=True,
            rewritten=rewritten,
            code="HALLUCINATION_RISK",
            message=f"Faithfulness {score:.2f} < {self._minimum}",
        )


class PIIScrubber:
    """Redact emails, phones, and SSNs from the answer."""

    @property
    def name(self) -> str:
        """Return ``pii_scrubber``."""
        return "pii_scrubber"

    def check(self, value: str) -> GuardrailResult:
        """Replace PII with placeholders.

        Args:
            value: Answer text.

        Returns:
            Always passes with possible redactions.
        """
        scrubbed = _EMAIL_RE.sub("[REDACTED_EMAIL]", value)
        scrubbed = _PHONE_RE.sub("[REDACTED_PHONE]", scrubbed)
        scrubbed = _SSN_RE.sub("[REDACTED_SSN]", scrubbed)
        return GuardrailResult(passed=True, rewritten=scrubbed)


def evidence_from_response(response: QueryResponse, sql_rows: list[SqlRow] | None = None) -> str:
    """Build a single evidence string from a response and optional SQL rows.

    Args:
        response: Agent response (uses ``sources`` and ``sql_used``).
        sql_rows: Optional raw SQL rows.

    Returns:
        Concatenated evidence text.
    """
    parts = [item.text for item in response.sources]
    if response.sql_used:
        parts.append(response.sql_used)
    if sql_rows:
        parts.extend(str(row.fields) for row in sql_rows)
    return "\n".join(parts)
