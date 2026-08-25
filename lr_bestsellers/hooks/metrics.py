"""In-memory metrics counters, histograms, and alert threshold checks."""

from __future__ import annotations

from collections import defaultdict
from typing import Final

import structlog
from pydantic import BaseModel

log = structlog.get_logger(__name__)

THRESHOLD_FAIL_RATE: Final[float] = 0.20
INJECTION_BURST: Final[int] = 5
HALLUCINATION_RATE: Final[float] = 0.05
SQL_BYTES_ALERT: Final[int] = 5 * 1024 * 1024 * 1024


class Alert(BaseModel):
    """A fired alert from metric thresholds.

    Attributes:
        name: Alert identifier.
        message: Human-readable detail.
        value: Observed value that crossed the threshold.
    """

    name: str
    message: str
    value: float


class MetricsRegistry:
    """Process-local counters and histograms.

    Not a Prometheus client — a small registry the callback handler and eval
    runner can inspect. Swap for a remote backend at the process boundary.
    """

    def __init__(self) -> None:
        """Initialise empty metric maps."""
        self._counters: dict[str, int] = defaultdict(int)
        self._histograms: dict[str, list[float]] = defaultdict(list)

    def incr(self, name: str, amount: int = 1) -> None:
        """Increment a counter.

        Args:
            name: Metric name.
            amount: Delta (default 1).
        """
        self._counters[name] += amount

    def observe(self, name: str, value: float) -> None:
        """Record a histogram sample.

        Args:
            name: Metric name.
            value: Observed value.
        """
        self._histograms[name].append(value)

    def get_counter(self, name: str) -> int:
        """Return a counter value.

        Args:
            name: Metric name.

        Returns:
            Current count (0 if unseen).
        """
        return int(self._counters.get(name, 0))

    def get_histogram(self, name: str) -> list[float]:
        """Return histogram samples.

        Args:
            name: Metric name.

        Returns:
            Copy of samples.
        """
        return list(self._histograms.get(name, []))

    def check_alerts(self) -> list[Alert]:
        """Evaluate alert rules against current metrics.

        Returns:
            Zero or more ``Alert`` objects.
        """
        alerts: list[Alert] = []
        queries = max(1, self.get_counter("queries.total"))
        threshold_fails = self.get_counter("threshold.failed")
        fail_rate = threshold_fails / queries
        if fail_rate > THRESHOLD_FAIL_RATE and queries >= 5:
            alerts.append(
                Alert(
                    name="knowledge_gap",
                    message="Threshold failures exceeded 20% of queries",
                    value=fail_rate,
                )
            )
        injections = self.get_counter("guardrail.injection")
        if injections > INJECTION_BURST:
            alerts.append(
                Alert(
                    name="security_injection",
                    message="More than 5 injection guardrail failures",
                    value=float(injections),
                )
            )
        hallu = self.get_counter("hallucination.risk")
        if hallu / queries > HALLUCINATION_RATE and queries >= 5:
            alerts.append(
                Alert(
                    name="quality_regression",
                    message="Hallucination risk rate exceeded 5%",
                    value=hallu / queries,
                )
            )
        for nbytes in self.get_histogram("sql.bytes"):
            if nbytes > SQL_BYTES_ALERT:
                alerts.append(
                    Alert(
                        name="sql_cost",
                        message="SQL job estimated over 5 GiB",
                        value=nbytes,
                    )
                )
                break
        for alert in alerts:
            log.error("metrics.alert", name=alert.name, value=alert.value, message=alert.message)
        return alerts


_REGISTRY = MetricsRegistry()


def get_metrics() -> MetricsRegistry:
    """Return the process-wide metrics registry.

    Returns:
        Shared ``MetricsRegistry``.
    """
    return _REGISTRY


def reset_metrics() -> None:
    """Replace the process-wide registry (tests only)."""
    global _REGISTRY
    _REGISTRY = MetricsRegistry()
