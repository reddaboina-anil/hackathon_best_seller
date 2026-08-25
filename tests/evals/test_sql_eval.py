"""Offline SQL validity and intent-classification evals."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from lr_bestsellers.agent.nodes import keyword_intent
from lr_bestsellers.guardrails.sql import SelectOnlyGuardrail, TableAllowlistGuardrail


class SqlCase(BaseModel):
    """One SQL ground-truth example.

    Attributes:
        query: User question.
        sql: Expected or candidate SQL.
        valid: Whether the SQL should pass guardrails.
    """

    query: str
    sql: str
    valid: bool


class IntentCase(BaseModel):
    """One labelled intent example.

    Attributes:
        query: User question.
        intent: Gold intent label.
    """

    query: str
    intent: str


def evaluate_sql(path: Path) -> dict[str, float]:
    """Measure SELECT-only + allowlist validity rate.

    Args:
        path: JSONL of ``SqlCase``.

    Returns:
        ``sql_validity_rate`` in ``[0, 1]``.
    """
    select_only = SelectOnlyGuardrail()
    allowlist = TableAllowlistGuardrail()
    correct = 0
    total = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        case = SqlCase.model_validate(json.loads(line))
        total += 1
        passed = select_only.check(case.sql).passed and allowlist.check(case.sql).passed
        if passed == case.valid:
            correct += 1
    return {"sql_validity_rate": correct / max(1, total)}


def evaluate_intent(path: Path) -> dict[str, float]:
    """Measure keyword intent classifier accuracy on golden queries.

    Args:
        path: JSONL with ``query`` and ``intent`` fields.

    Returns:
        ``intent_classification_accuracy``.
    """
    correct = 0
    total = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        case = IntentCase(query=str(payload["query"]), intent=str(payload["intent"]))
        total += 1
        if keyword_intent(case.query) == case.intent:
            correct += 1
    return {"intent_classification_accuracy": correct / max(1, total)}
