"""Offline retrieval eval helpers (context recall / precision proxies)."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

from lr_bestsellers.store.sparse import tokenize


class RetrievalCase(BaseModel):
    """One retrieval ground-truth pair.

    Attributes:
        query: User question.
        relevant_doc_ids: Ids that should be retrieved.
        corpus: Mapping of doc id to text.
    """

    query: str
    relevant_doc_ids: list[str]
    corpus: dict[str, str] = Field(default_factory=dict)


def _rank_corpus(query: str, corpus: dict[str, str]) -> list[str]:
    """Rank corpus docs by token overlap with the query.

    Args:
        query: Question.
        corpus: Id → text.

    Returns:
        Doc ids best-first.
    """
    q = set(tokenize(query))
    scored: list[tuple[float, str]] = []
    for doc_id, text in corpus.items():
        tokens = set(tokenize(text))
        score = len(q & tokens) / max(1, len(q))
        scored.append((score, doc_id))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [doc_id for _, doc_id in scored]


def evaluate_retrieval(path: Path, k: int = 3) -> dict[str, float]:
    """Compute mean context recall and precision at k.

    Args:
        path: JSONL of ``RetrievalCase`` objects (corpus inlined).
        k: Cutoff.

    Returns:
        ``context_recall`` and ``context_precision`` means.
    """
    recalls: list[float] = []
    precisions: list[float] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        case = RetrievalCase.model_validate(json.loads(line))
        ranked = _rank_corpus(case.query, case.corpus)[:k]
        relevant = set(case.relevant_doc_ids)
        hit = [doc_id for doc_id in ranked if doc_id in relevant]
        recalls.append(len(set(hit) & relevant) / max(1, len(relevant)))
        precisions.append(len(hit) / max(1, len(ranked)))
    n = max(1, len(recalls))
    return {
        "context_recall": sum(recalls) / n,
        "context_precision": sum(precisions) / n,
    }
