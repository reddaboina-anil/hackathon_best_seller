"""Unit tests for the in-process metrics registry."""

from __future__ import annotations

from lr_bestsellers.hooks.metrics import MetricsRegistry


def test_counters_and_alerts() -> None:
    """Injection burst and expensive SQL fire alerts."""
    registry = MetricsRegistry()
    for _ in range(6):
        registry.incr("guardrail.injection")
    registry.observe("sql.bytes", 6 * 1024 * 1024 * 1024)
    alerts = {item.name for item in registry.check_alerts()}
    assert "security_injection" in alerts
    assert "sql_cost" in alerts


def test_knowledge_gap_alert() -> None:
    """Threshold-fail rate above 20% with enough volume fires knowledge_gap."""
    registry = MetricsRegistry()
    for _ in range(10):
        registry.incr("queries.total")
    for _ in range(4):
        registry.incr("threshold.failed")
    names = {item.name for item in registry.check_alerts()}
    assert "knowledge_gap" in names
