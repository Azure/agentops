"""Threshold evaluation logic for AgentOps."""

from __future__ import annotations

from typing import Dict, List

from agentops.core.models import ThresholdEvaluationResult, ThresholdRule


def evaluate_thresholds(
    threshold_rules: List[ThresholdRule],
    metrics_by_name: Dict[str, float],
) -> List[ThresholdEvaluationResult]:
    results: List[ThresholdEvaluationResult] = []

    for rule in threshold_rules:
        if rule.evaluator not in metrics_by_name:
            raise ValueError(
                f"Missing evaluator score required by threshold: {rule.evaluator}"
            )

        actual_value = metrics_by_name[rule.evaluator]

        if rule.criteria == "true" or rule.criteria == "false":
            expected_bool = rule.criteria == "true"

            if actual_value in (0.0, 1.0):
                actual_bool = actual_value == 1.0
            else:
                raise ValueError(
                    f"Evaluator '{rule.evaluator}' must produce 0/1 for boolean criteria"
                )

            passed = actual_bool is expected_bool
            results.append(
                ThresholdEvaluationResult(
                    evaluator=rule.evaluator,
                    criteria=rule.criteria,
                    expected="true" if expected_bool else "false",
                    actual="true" if actual_bool else "false",
                    passed=passed,
                )
            )
            continue

        if rule.value is None:
            raise ValueError(
                f"Threshold for evaluator '{rule.evaluator}' requires a numeric value"
            )

        target_value = float(rule.value)

        if rule.criteria == ">=":
            passed = actual_value >= target_value
        elif rule.criteria == ">":
            passed = actual_value > target_value
        elif rule.criteria == "<=":
            passed = actual_value <= target_value
        elif rule.criteria == "<":
            passed = actual_value < target_value
        elif rule.criteria == "==":
            passed = actual_value == target_value
        else:
            raise ValueError(f"Unsupported threshold criteria: {rule.criteria}")

        results.append(
            ThresholdEvaluationResult(
                evaluator=rule.evaluator,
                criteria=rule.criteria,
                expected=f"{target_value:.6f}",
                actual=f"{actual_value:.6f}",
                passed=passed,
            )
        )

    return results
