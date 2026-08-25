"""Input guardrails: length, PII, injection, banned topics, rate limit."""

from __future__ import annotations

import re
import time
from typing import Final

from pydantic import BaseModel, Field

from lr_bestsellers.guardrails.base import GuardrailResult

_EMAIL_RE: Final[re.Pattern[str]] = re.compile(
    r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b"
)
_PHONE_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}\b"
)
_SSN_RE: Final[re.Pattern[str]] = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_INJECTION_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.IGNORECASE),
    re.compile(r"<\|im_start\|>"),
    re.compile(r"<\|im_end\|>"),
    re.compile(r"system\s*:\s*you\s+are", re.IGNORECASE),
    re.compile(r"\[INST\]"),
)


class RateLimitConfig(BaseModel):
    """Token-bucket parameters.

    Attributes:
        capacity: Maximum tokens.
        refill_per_second: Tokens added each second.
        caller_id: Bucket key.
    """

    capacity: float = Field(20.0, gt=0.0)
    refill_per_second: float = Field(1.0, gt=0.0)
    caller_id: str = "default"


class LengthGuardrail:
    """Reject queries outside ``[min_len, max_len]`` characters."""

    def __init__(self, min_len: int = 1, max_len: int = 2000) -> None:
        """Store length bounds.

        Args:
            min_len: Inclusive minimum length.
            max_len: Inclusive maximum length.
        """
        self._min = min_len
        self._max = max_len

    @property
    def name(self) -> str:
        """Return ``length``."""
        return "length"

    def check(self, value: str) -> GuardrailResult:
        """Validate query length.

        Args:
            value: User query.

        Returns:
            Fail with ``QUERY_TOO_SHORT`` or ``QUERY_TOO_LONG``.
        """
        length = len(value)
        if length < self._min:
            return GuardrailResult(
                passed=False,
                code="QUERY_TOO_SHORT",
                message=f"Query length {length} < {self._min}",
            )
        if length > self._max:
            return GuardrailResult(
                passed=False,
                code="QUERY_TOO_LONG",
                message=f"Query length {length} > {self._max}",
            )
        return GuardrailResult(passed=True, rewritten=value)


class PIIGuardrail:
    """Reject emails, US phones, and SSNs. Never include the raw query in ``message``."""

    @property
    def name(self) -> str:
        """Return ``pii``."""
        return "pii"

    def check(self, value: str) -> GuardrailResult:
        """Scan for PII patterns.

        Args:
            value: User query.

        Returns:
            Fail with ``PII_DETECTED`` without echoing the query.
        """
        if _EMAIL_RE.search(value) or _PHONE_RE.search(value) or _SSN_RE.search(value):
            return GuardrailResult(
                passed=False,
                code="PII_DETECTED",
                message="Query contains PII and was rejected",
            )
        return GuardrailResult(passed=True, rewritten=value)


class PromptInjectionGuardrail:
    """Reject known prompt-injection phrases and chat markup."""

    @property
    def name(self) -> str:
        """Return ``prompt_injection``."""
        return "prompt_injection"

    def check(self, value: str) -> GuardrailResult:
        """Scan for injection patterns.

        Args:
            value: User query.

        Returns:
            Fail with ``INJECTION_ATTEMPT``.
        """
        for pattern in _INJECTION_PATTERNS:
            if pattern.search(value):
                return GuardrailResult(
                    passed=False,
                    code="INJECTION_ATTEMPT",
                    message="Query looks like a prompt injection attempt",
                )
        return GuardrailResult(passed=True, rewritten=value)


class BannedTopicsGuardrail:
    """Reject queries containing configured banned substrings.

    Args:
        topics: Lowercased substrings that are not allowed.
    """

    def __init__(self, topics: list[str] | None = None) -> None:
        """Store the blocklist.

        Args:
            topics: Banned phrases; default includes a test-friendly token.
        """
        default = ["exfiltrate production secrets"]
        self._topics = [item.lower() for item in (topics or default)]

    @property
    def name(self) -> str:
        """Return ``banned_topics``."""
        return "banned_topics"

    def check(self, value: str) -> GuardrailResult:
        """Scan for banned topics.

        Args:
            value: User query.

        Returns:
            Fail with ``BANNED_TOPIC``.
        """
        lowered = value.lower()
        for topic in self._topics:
            if topic and topic in lowered:
                return GuardrailResult(
                    passed=False,
                    code="BANNED_TOPIC",
                    message="Query matches a banned topic",
                )
        return GuardrailResult(passed=True, rewritten=value)


class RateLimitGuardrail:
    """Per-caller token bucket.

    Args:
        config: Bucket parameters. Tests may pass a shared instance.
        buckets: Optional shared mutable map of caller_id → (tokens, timestamp).
    """

    def __init__(
        self,
        config: RateLimitConfig | None = None,
        buckets: dict[str, tuple[float, float]] | None = None,
    ) -> None:
        """Initialise bucket state.

        Args:
            config: Rate-limit configuration.
            buckets: Shared store for tokens (injected in tests).
        """
        self._config = config or RateLimitConfig()
        self._buckets = buckets if buckets is not None else {}

    @property
    def name(self) -> str:
        """Return ``rate_limit``."""
        return "rate_limit"

    def check(self, value: str) -> GuardrailResult:
        """Consume one token for the configured caller.

        Args:
            value: Unused query text (bucket key comes from config).

        Returns:
            Fail with ``RATE_LIMIT_EXCEEDED`` when empty.
        """
        del value
        now = time.monotonic()
        caller = self._config.caller_id
        tokens, last = self._buckets.get(caller, (self._config.capacity, now))
        elapsed = max(0.0, now - last)
        tokens = min(self._config.capacity, tokens + elapsed * self._config.refill_per_second)
        if tokens < 1.0:
            self._buckets[caller] = (tokens, now)
            return GuardrailResult(
                passed=False,
                code="RATE_LIMIT_EXCEEDED",
                message="Caller exceeded the query rate limit",
            )
        self._buckets[caller] = (tokens - 1.0, now)
        return GuardrailResult(passed=True)
