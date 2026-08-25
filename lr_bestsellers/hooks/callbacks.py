"""LangGraph/LangChain callback handler for segment-intelligence traces."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog
from langchain_core.callbacks.base import BaseCallbackHandler

from lr_bestsellers.hooks.metrics import MetricsRegistry, get_metrics

log = structlog.get_logger(__name__)


class SegmentIntelligenceCallbackHandler(BaseCallbackHandler):
    """Record node/tool/LLM events onto a ``MetricsRegistry``.

    Args:
        metrics: Optional registry (defaults to the process singleton).
    """

    def __init__(self, metrics: MetricsRegistry | None = None) -> None:
        """Store the metrics registry.

        Args:
            metrics: Counters/histograms sink.
        """
        super().__init__()
        self._metrics = metrics or get_metrics()

    def on_chain_start(
        self,
        serialized: dict[str, Any],
        inputs: dict[str, Any],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """Count graph/node starts.

        Args:
            serialized: LangChain serialized component.
            inputs: Chain inputs.
            run_id: Run id.
            parent_run_id: Parent run id.
            tags: Optional tags.
            metadata: Optional metadata.
            **kwargs: Extra callback fields.
        """
        del inputs, parent_run_id, tags, metadata, kwargs
        safe = serialized or {}
        name = safe.get("name") or safe.get("id") or "chain"
        log.info("hook.chain_start", name=str(name), run_id=str(run_id))
        self._metrics.incr("chain.start")

    def on_chain_end(
        self,
        outputs: dict[str, Any],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        """Count graph/node completions and inspect state flags.

        Args:
            outputs: Chain outputs.
            run_id: Run id.
            parent_run_id: Parent run id.
            **kwargs: Extra callback fields.
        """
        del parent_run_id, kwargs
        log.info("hook.chain_end", run_id=str(run_id))
        self._metrics.incr("chain.end")
        if isinstance(outputs, dict) and outputs.get("threshold_failed"):
            self.on_threshold_failed()
        if isinstance(outputs, dict) and outputs.get("sql_used"):
            self.on_sql_executed(str(outputs["sql_used"]), bytes_processed=0)

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        """Count LLM calls.

        Args:
            serialized: Serialized LLM.
            prompts: Prompt strings.
            run_id: Run id.
            parent_run_id: Parent run id.
            **kwargs: Extra fields.
        """
        del serialized, prompts, parent_run_id, kwargs
        log.info("hook.llm_start", run_id=str(run_id))
        self._metrics.incr("llm.calls")

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        """Count tool invocations.

        Args:
            serialized: Serialized tool.
            input_str: Tool input.
            run_id: Run id.
            parent_run_id: Parent run id.
            **kwargs: Extra fields.
        """
        del input_str, parent_run_id, kwargs
        name = serialized.get("name") or "tool"
        log.info("hook.tool_start", name=str(name), run_id=str(run_id))
        self._metrics.incr("tool.calls")

    def on_threshold_failed(self) -> None:
        """Record a retrieval threshold miss (knowledge-gap signal)."""
        log.info("hook.threshold_failed")
        self._metrics.incr("threshold.failed")

    def on_guardrail_failed(self, code: str) -> None:
        """Record a guardrail failure.

        Args:
            code: Guardrail machine code (e.g. ``INJECTION_ATTEMPT``).
        """
        log.info("hook.guardrail_failed", code=code)
        self._metrics.incr("guardrail.failed")
        if code == "INJECTION_ATTEMPT":
            self._metrics.incr("guardrail.injection")

    def on_hallucination_risk(self, score: float) -> None:
        """Record a faithfulness warning.

        Args:
            score: Faithfulness score that missed the 0.80 bar.
        """
        log.error("hook.hallucination_risk", score=score)
        self._metrics.incr("hallucination.risk")
        self._metrics.observe("hallucination.score", score)

    def on_sql_executed(self, sql: str, bytes_processed: int) -> None:
        """Record a SQL execution and optional bytes histogram.

        Args:
            sql: Statement that ran (truncated in logs).
            bytes_processed: Estimated or billed bytes.
        """
        log.info("hook.sql_executed", sql=sql[:120], bytes=bytes_processed)
        self._metrics.incr("sql.executed")
        if bytes_processed:
            self._metrics.observe("sql.bytes", float(bytes_processed))
