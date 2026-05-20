"""Catalog invariants for the doctor check list."""

from __future__ import annotations

import re
from typing import Set

from agentops.agent.checks.catalog import (
    CATEGORY_DESCRIPTIONS,
    CATEGORY_ORDER,
    CHECKS,
    FLAG_LABELS,
    SOURCE_LABELS,
    by_category,
    filter_checks,
    reference_url_for,
)
from agentops.agent.checks.posture_rules import RULE_REGISTRY
from agentops.agent.findings import Category, Severity
from agentops.agent.llm_assist._engine import _ALL_RULES


def test_catalog_ids_are_unique() -> None:
    ids = [spec.id for spec in CHECKS]
    assert len(ids) == len(set(ids)), "duplicate check ids in catalog"


def test_catalog_covers_every_pillar() -> None:
    grouped = by_category()
    missing = [cat.value for cat in CATEGORY_ORDER if not grouped.get(cat)]
    assert missing == [], (
        f"categories without any catalog entry: {missing}"
    )


def test_catalog_categories_are_valid() -> None:
    for spec in CHECKS:
        assert isinstance(spec.category, Category)


def test_catalog_severities_are_valid_and_non_empty() -> None:
    for spec in CHECKS:
        assert spec.severities, f"{spec.id} has no severities"
        for sev in spec.severities:
            assert isinstance(sev, Severity)


def test_catalog_required_sources_are_known() -> None:
    for spec in CHECKS:
        for req in spec.requires:
            assert req in SOURCE_LABELS, (
                f"{spec.id} requires unknown source {req!r}"
            )


def test_catalog_flags_are_known() -> None:
    for spec in CHECKS:
        for flag in spec.flags:
            assert flag in FLAG_LABELS, (
                f"{spec.id} has unknown flag {flag!r}"
            )


def test_catalog_descriptions_match_pillars() -> None:
    for cat in CATEGORY_ORDER:
        assert cat in CATEGORY_DESCRIPTIONS, cat


def test_catalog_exposes_public_reference_urls() -> None:
    for spec in CHECKS:
        url = reference_url_for(spec)
        assert url is not None, f"{spec.id} has no public reference URL"
        assert url.startswith("https://"), (
            f"{spec.id} reference URL is not clickable: {url!r}"
        )


def test_catalog_includes_every_posture_rule_id() -> None:
    catalog_ids: Set[str] = {spec.id for spec in CHECKS}
    missing = set(RULE_REGISTRY.keys()) - catalog_ids
    assert missing == set(), (
        f"posture rules not described in catalog: {sorted(missing)}"
    )


def test_catalog_includes_every_llm_assist_rule_id() -> None:
    """Every entry in `_ALL_RULES` must map to a catalog entry.

    The names in ``_ALL_RULES`` are the config-side rule keys used in
    ``agent.yaml`` to enable / disable individual LLM-judged rules. The
    *finding* ids emitted by those rules use a slightly different
    namespace (e.g. ``rai.prompt_transparency`` -> finding id
    ``responsible_ai.llm.prompt_transparency``). The catalog stores the
    finding ids, so this test bridges the two namespaces explicitly.
    """
    config_to_finding = {
        "rai.prompt_transparency": "responsible_ai.llm.prompt_transparency",
        "rai.prompt_safety_guardrails": "responsible_ai.llm.prompt_safety_guardrails",
        "rai.prompt_jailbreak_surface": "responsible_ai.llm.prompt_jailbreak_surface",
        "rai.dataset_pii_risk": "responsible_ai.llm.dataset_pii_risk",
        "rai.dataset_bias_signals": "responsible_ai.llm.dataset_bias_signals",
        "opex.bundle_coverage": "opex.llm.bundle_coverage",
        "opex.spec_conformance.llm.implementation_gap": (
            "opex.spec_conformance.llm.implementation_gap"
        ),
    }
    # Sanity: the mapping covers exactly the published rule set.
    assert set(config_to_finding.keys()) == set(_ALL_RULES), (
        "LLM-assist rule list changed - update the mapping in this test"
    )

    catalog_ids: Set[str] = {spec.id for spec in CHECKS}
    missing = [
        finding_id
        for finding_id in config_to_finding.values()
        if finding_id not in catalog_ids
    ]
    assert missing == [], (
        f"LLM-assist findings not described in catalog: {missing}"
    )


def test_catalog_dynamic_ids_use_placeholder_syntax() -> None:
    placeholder = re.compile(r"<[a-z_]+>")
    for spec in CHECKS:
        if spec.is_dynamic:
            assert placeholder.search(spec.id), (
                f"{spec.id} is marked dynamic but has no <placeholder>"
            )


def test_filter_checks_by_category() -> None:
    security_only = filter_checks(category=Category.SECURITY)
    assert security_only, "security pillar should have at least one check"
    assert all(s.category is Category.SECURITY for s in security_only)


def test_filter_checks_by_source() -> None:
    foundry_only = filter_checks(source="foundry_control")
    assert foundry_only, "expected catalog entries depending on foundry_control"
    assert all("foundry_control" in s.requires for s in foundry_only)


def test_filter_combination_is_an_intersection() -> None:
    intersect = filter_checks(
        category=Category.RESPONSIBLE_AI, source="judge_model"
    )
    assert intersect, "expected at least one RAI LLM-judged check"
    for spec in intersect:
        assert spec.category is Category.RESPONSIBLE_AI
        assert "judge_model" in spec.requires
