"""Guardrail chains for input, SQL, and output."""

from __future__ import annotations

from lr_bestsellers.exceptions import InputGuardrailError, SQLGuardrailError
from lr_bestsellers.guardrails.base import GuardrailChain
from lr_bestsellers.guardrails.input import (
    BannedTopicsGuardrail,
    LengthGuardrail,
    PIIGuardrail,
    PromptInjectionGuardrail,
    RateLimitConfig,
    RateLimitGuardrail,
)
from lr_bestsellers.guardrails.sql import (
    BytesEstimatorProtocol,
    CostEstimationGuardrail,
    RowLimitGuardrail,
    SelectOnlyGuardrail,
    TableAllowlistGuardrail,
)


def build_input_chain(caller_id: str = "default") -> GuardrailChain:
    """Build the default input guardrail chain.

    Args:
        caller_id: Rate-limit bucket key.

    Returns:
        Chain that raises ``InputGuardrailError``.
    """
    return GuardrailChain(
        [
            LengthGuardrail(),
            PIIGuardrail(),
            PromptInjectionGuardrail(),
            BannedTopicsGuardrail(),
            RateLimitGuardrail(RateLimitConfig(caller_id=caller_id)),
        ],
        error_cls=InputGuardrailError,
    )


def build_sql_chain(
    estimator: BytesEstimatorProtocol | None = None,
) -> GuardrailChain:
    """Build the default SQL guardrail chain.

    Args:
        estimator: Optional dry-run bytes estimator.

    Returns:
        Chain that raises ``SQLGuardrailError``.
    """
    cost = CostEstimationGuardrail(estimator=estimator)
    return GuardrailChain(
        [
            SelectOnlyGuardrail(),
            TableAllowlistGuardrail(),
            RowLimitGuardrail(),
            cost,
        ],
        error_cls=SQLGuardrailError,
    )


class SqlChainValidator:
    """``SqlValidatorProtocol`` adapter around :func:`build_sql_chain`.

    Args:
        chain: SQL guardrail chain.
    """

    def __init__(self, chain: GuardrailChain | None = None) -> None:
        """Store the chain.

        Args:
            chain: SQL chain; default :func:`build_sql_chain`.
        """
        self._chain = chain or build_sql_chain()

    def validate(self, sql: str) -> str:
        """Run SQL guardrails and return possibly rewritten SQL.

        Args:
            sql: Candidate statement.

        Returns:
            SQL allowed to execute.

        Raises:
            SQLGuardrailError: When a guardrail rejects the statement.
        """
        result = self._chain.run(sql)
        return result.rewritten or sql
