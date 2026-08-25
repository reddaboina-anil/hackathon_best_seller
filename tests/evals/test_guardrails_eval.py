"""Adversarial eval: input guardrails must block attack queries."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from lr_bestsellers.exceptions import InputGuardrailError
from lr_bestsellers.guardrails.base import GuardrailChain
from lr_bestsellers.guardrails.input import (
    BannedTopicsGuardrail,
    LengthGuardrail,
    PIIGuardrail,
    PromptInjectionGuardrail,
)


class AdversarialCase(BaseModel):
    """One attack or abuse query.

    Attributes:
        query: Malicious or out-of-policy text.
        expected_code: Guardrail code that should fire.
    """

    query: str
    expected_code: str


def _chain_without_rate_limit() -> GuardrailChain:
    """Input chain that will not flake on volume.

    Returns:
        Chain excluding the token bucket.
    """
    return GuardrailChain(
        [
            LengthGuardrail(),
            PIIGuardrail(),
            PromptInjectionGuardrail(),
            BannedTopicsGuardrail(),
        ],
        error_cls=InputGuardrailError,
    )


def evaluate_guardrails(path: Path) -> dict[str, float]:
    """Fraction of adversarial queries that are blocked with the expected code.

    Args:
        path: JSONL of ``AdversarialCase``.

    Returns:
        ``adversarial_block_rate``.
    """
    chain = _chain_without_rate_limit()
    blocked = 0
    total = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        case = AdversarialCase.model_validate(json.loads(line))
        total += 1
        try:
            chain.run(case.query)
        except InputGuardrailError as exc:
            if exc.code == case.expected_code:
                blocked += 1
    return {"adversarial_block_rate": blocked / max(1, total)}
