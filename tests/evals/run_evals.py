"""Eval runner: offline metrics, JSON report, non-zero exit on CI gate misses."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel, Field


def _load(mod_name: str) -> Any:
    """Load a sibling eval module by file path.

    Args:
        mod_name: Module filename stem.

    Returns:
        Loaded module.
    """
    path = Path(__file__).resolve().parent / f"{mod_name}.py"
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_generation = _load("test_generation_eval")
_guardrails = _load("test_guardrails_eval")
_retrieval = _load("test_retrieval_eval")
_sql = _load("test_sql_eval")
evaluate_generation = _generation.evaluate_generation
evaluate_guardrails = _guardrails.evaluate_guardrails
evaluate_retrieval = _retrieval.evaluate_retrieval
evaluate_intent = _sql.evaluate_intent
evaluate_sql = _sql.evaluate_sql

CI_GATES: Final[dict[str, float]] = {
    "faithfulness": 0.90,
    "context_recall": 0.85,
    "sql_validity_rate": 0.95,
}


class EvalReport(BaseModel):
    """Serialized eval output.

    Attributes:
        date: ISO date of the run.
        metrics: Metric name to score.
        passed: Whether CI gates passed.
        failures: Gate names that missed their target.
    """

    date: str
    metrics: dict[str, float] = Field(default_factory=dict)
    passed: bool = True
    failures: list[str] = Field(default_factory=list)


def dataset_dir() -> Path:
    """Return the datasets directory.

    Returns:
        ``tests/evals/datasets``.
    """
    return Path(__file__).resolve().parent / "datasets"


def results_dir() -> Path:
    """Return the results directory.

    Returns:
        ``tests/evals/results``.
    """
    return Path(__file__).resolve().parent / "results"


def run_all() -> EvalReport:
    """Execute all offline eval suites.

    Returns:
        Combined ``EvalReport``.
    """
    base = dataset_dir()
    metrics: dict[str, float] = {}
    metrics.update(evaluate_retrieval(base / "retrieval_test_set.jsonl"))
    metrics.update(evaluate_generation(base / "golden_queries.jsonl"))
    metrics.update(evaluate_sql(base / "sql_test_set.jsonl"))
    metrics.update(evaluate_intent(base / "golden_queries.jsonl"))
    metrics.update(evaluate_guardrails(base / "adversarial_set.jsonl"))
    failures = [name for name, minimum in CI_GATES.items() if metrics.get(name, 0.0) < minimum]
    return EvalReport(
        date=date.today().isoformat(),
        metrics=metrics,
        passed=not failures,
        failures=failures,
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Optional argument vector.

    Returns:
        Exit code (1 when CI gates fail).
    """
    parser = argparse.ArgumentParser(description="Run lr-bestsellers evals")
    parser.add_argument("--report", action="store_true", help="Write JSON report")
    args = parser.parse_args(argv)
    report = run_all()
    payload = report.model_dump()
    rendered = json.dumps(payload, indent=2)
    sys.stdout.write(rendered + "\n")
    if args.report:
        out_dir = results_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{report.date}.json").write_text(rendered + "\n", encoding="utf-8")
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
