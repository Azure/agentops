"""Tests for the WAF knowledge-base loader."""

from __future__ import annotations

from agentops.agent.knowledge import (
    find_waf_item,
    load_waf_checklist,
    waf_index_by_check_id,
)


def test_checklist_loads_with_rows() -> None:
    items = load_waf_checklist()
    assert items, "WAF checklist CSV should ship at least one row"


def test_every_row_has_required_fields() -> None:
    for item in load_waf_checklist():
        assert item.item_id, "item_id missing"
        assert item.doctor_check_id, "doctor_check_id required (strict scope)"
        assert item.status == "implemented", (
            f"status must be 'implemented' (no planned rows). Got "
            f"{item.status!r} for {item.item_id}"
        )
        assert item.pillar, f"pillar missing on {item.item_id}"
        assert item.area, f"area missing on {item.item_id}"
        assert item.reference_url.startswith("https://learn.microsoft.com/"), (
            f"reference_url must be a public Microsoft Learn link "
            f"(got {item.reference_url!r} on {item.item_id})"
        )


def test_item_ids_are_unique() -> None:
    ids = [item.item_id for item in load_waf_checklist()]
    assert len(ids) == len(set(ids)), "duplicate item_id in CSV"


def test_doctor_check_ids_are_unique() -> None:
    check_ids = [item.doctor_check_id for item in load_waf_checklist()]
    assert len(check_ids) == len(set(check_ids)), (
        "duplicate doctor_check_id in CSV — the prefix index would lose rows"
    )


def test_find_waf_item_exact_match() -> None:
    item = find_waf_item("opex.no_pr_gate")
    assert item is not None
    assert item.pillar == "OperationalExcellence"


def test_find_waf_item_prefix_match() -> None:
    # `regression` row should match concrete `regression.coherence` ids.
    item = find_waf_item("regression.coherence")
    assert item is not None
    assert item.doctor_check_id == "regression"


def test_find_waf_item_longest_prefix_wins() -> None:
    # `safety` is a prefix row; `safety.runtime.content_filter` is a
    # more specific row. The specific one must win.
    specific = find_waf_item("safety.runtime.content_filter")
    generic = find_waf_item("safety.violence")
    assert specific is not None
    assert generic is not None
    assert specific.doctor_check_id == "safety.runtime.content_filter"
    assert generic.doctor_check_id == "safety"


def test_find_waf_item_unknown_returns_none() -> None:
    assert find_waf_item("unknown.finding.id") is None
    assert find_waf_item("") is None


def test_find_waf_item_for_new_genaiops_rules() -> None:
    """Every new GenAIOps rule must resolve via the longest-prefix lookup."""
    for finding_id in (
        "opex.unversioned_bundle",
        "opex.results_dir_bloat",
        "opex.workflow_concurrency_lock",
        "opex.workflow_action_sha_pinning",
        "opex.no_foundry_control_configured",
    ):
        item = find_waf_item(finding_id)
        assert item is not None, f"{finding_id} not found in CSV"
        assert item.pillar == "OperationalExcellence"


def test_find_waf_item_flaky_metric_prefix_match() -> None:
    # CSV has `opex.flaky_metric`; the runtime emits e.g.
    # `opex.flaky_metric.coherence`. Longest-prefix lookup must match.
    item = find_waf_item("opex.flaky_metric.coherence")
    assert item is not None
    assert item.doctor_check_id == "opex.flaky_metric"


def test_index_keys_match_load() -> None:
    items = load_waf_checklist()
    index = waf_index_by_check_id()
    assert set(index.keys()) == {item.doctor_check_id for item in items}


# ---------------------------------------------------------------------------
# Workspace override
# ---------------------------------------------------------------------------


def test_workspace_override_replaces_packaged_row(tmp_path) -> None:
    # Pick an existing packaged id and override its pillar.
    workspace_dir = tmp_path / ".agentops"
    workspace_dir.mkdir()
    (workspace_dir / "waf-checklist.csv").write_text(
        "pillar,area,item_id,title,detection_source,detection_signal,"
        "doctor_check_id,status,reference_url\n"
        "CustomPillar,CustomArea,waf.custom.override,Override title,"
        "workspace_files,custom signal,opex.no_pr_gate,implemented,"
        "https://learn.microsoft.com/custom\n",
        encoding="utf-8",
    )
    item = find_waf_item("opex.no_pr_gate", workspace=tmp_path)
    assert item is not None
    assert item.pillar == "CustomPillar"
    assert item.area == "CustomArea"
    assert item.reference_url == "https://learn.microsoft.com/custom"


def test_workspace_override_extends_with_new_id(tmp_path) -> None:
    workspace_dir = tmp_path / ".agentops"
    workspace_dir.mkdir()
    (workspace_dir / "waf-checklist.csv").write_text(
        "pillar,area,item_id,title,detection_source,detection_signal,"
        "doctor_check_id,status,reference_url\n"
        "Custom,Area,waf.custom.new,Brand new rule,workspace_files,"
        "my custom signal,my.team.custom_rule,implemented,"
        "https://learn.microsoft.com/custom\n",
        encoding="utf-8",
    )
    item = find_waf_item("my.team.custom_rule", workspace=tmp_path)
    assert item is not None
    assert item.item_id == "waf.custom.new"

    # Packaged ids still resolve.
    assert find_waf_item("opex.no_pr_gate", workspace=tmp_path) is not None


def test_workspace_override_skips_comment_lines(tmp_path) -> None:
    workspace_dir = tmp_path / ".agentops"
    workspace_dir.mkdir()
    (workspace_dir / "waf-checklist.csv").write_text(
        "# this is a comment\n"
        "# more comments\n"
        "pillar,area,item_id,title,detection_source,detection_signal,"
        "doctor_check_id,status,reference_url\n"
        "Custom,Area,waf.custom.new,New rule,workspace_files,sig,"
        "my.custom.id,implemented,https://learn.microsoft.com/x\n",
        encoding="utf-8",
    )
    assert find_waf_item("my.custom.id", workspace=tmp_path) is not None


def test_no_workspace_override_returns_packaged_only(tmp_path) -> None:
    # No .agentops/waf-checklist.csv present.
    item = find_waf_item("opex.no_pr_gate", workspace=tmp_path)
    assert item is not None
    # Should match the packaged row, not anything custom.
    assert item.pillar == "OperationalExcellence"


def test_workspace_path_none_returns_packaged_only() -> None:
    items_default = load_waf_checklist()
    items_none = load_waf_checklist(workspace=None)
    assert {i.doctor_check_id for i in items_default} == {
        i.doctor_check_id for i in items_none
    }
