"""Threshold evaluation against parsed :class:`Threshold` rules."""

from __future__ import annotations

from typing import Dict, List

from agentops.core.agentops_config import Threshold
from agentops.core.results import ThresholdEvaluation


def evaluate(
    rules: List[Threshold],
    metrics: Dict[str, float],
) -> List[ThresholdEvaluation]:
    """Apply each rule against the aggregate metric value.

    Missing metrics produce a failed evaluation with ``actual="missing"`` so
    the report can show the gap clearly rather than crashing the run.
    """
    results: List[ThresholdEvaluation] = []
    for rule in rules:
        actual_value = metrics.get(rule.metric)

        if rule.criteria in {"true", "false"}:
            expected = rule.criteria
            actual = "missing"
            passed = False
            if actual_value is not None:
                actual_bool = actual_value == 1.0
                actual = "true" if actual_bool else "false"
                passed = actual == expected
            results.append(
                ThresholdEvaluation(
                    metric=rule.metric,
                    criteria=rule.criteria,
                    expected=expected,
                    actual=actual,
                    passed=passed,
                )
            )
            continue

        if rule.value is None:
            raise ValueError(
                f"threshold for {rule.metric!r} requires a numeric value"
            )

        target = float(rule.value)
        expected_str = f"{rule.criteria}{target:g}"
        if actual_value is None:
            results.append(
                ThresholdEvaluation(
                    metric=rule.metric,
                    criteria=rule.criteria,
                    expected=expected_str,
                    actual="missing",
                    passed=False,
                )
            )
            continue

        if rule.criteria == ">=":
            passed = actual_value >= target
        elif rule.criteria == ">":
            passed = actual_value > target
        elif rule.criteria == "<=":
            passed = actual_value <= target
        elif rule.criteria == "<":
            passed = actual_value < target
        elif rule.criteria == "==":
            passed = actual_value == target
        else:
            raise ValueError(f"unsupported criteria {rule.criteria!r}")

        results.append(
            ThresholdEvaluation(
                metric=rule.metric,
                criteria=rule.criteria,
                expected=expected_str,
                actual=f"{actual_value:g}",
                passed=passed,
            )
        )
    return results
