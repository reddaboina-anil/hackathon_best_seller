"""Offline generation eval helpers (faithfulness / answer relevance proxies)."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from lr_bestsellers.guardrails.output import LexicalFaithfulnessScorer
from lr_bestsellers.store.sparse import tokenize


class GenerationCase(BaseModel):
    """One golden generation example.

    Attributes:
        query: User question.
        expected_answer: Reference answer.
        contexts: Evidence strings.
    """

    query: str
    expected_answer: str
    contexts: list[str]


def evaluate_generation(path: Path) -> dict[str, float]:
    """Score lexical faithfulness and answer relevance.

    Args:
        path: JSONL of ``GenerationCase`` (golden_queries.jsonl).

    Returns:
        Mean ``faithfulness`` and ``answer_relevance``.
    """
    scorer = LexicalFaithfulnessScorer()
    faith: list[float] = []
    relevance: list[float] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        case = GenerationCase.model_validate(json.loads(line))
        evidence = " ".join(case.contexts)
        faith.append(scorer.score(case.expected_answer, evidence))
        q_tokens = set(tokenize(case.query))
        a_tokens = set(tokenize(case.expected_answer))
        relevance.append(len(q_tokens & a_tokens) / max(1, len(q_tokens)))
    n = max(1, len(faith))
    return {
        "faithfulness": sum(faith) / n,
        "answer_relevance": sum(relevance) / n,
    }
