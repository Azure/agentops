"""Regression tests for the repository's CI workflow."""

from pathlib import Path

import yaml


_CI_WORKFLOW = Path(__file__).parents[2] / ".github" / "workflows" / "ci.yml"


def _jobs() -> dict:
    workflow = yaml.safe_load(_CI_WORKFLOW.read_text(encoding="utf-8"))
    return workflow["jobs"]


def test_publish_dev_exposes_version_from_built_wheel_metadata() -> None:
    publish_dev = _jobs()["publish-dev"]

    assert publish_dev["outputs"]["version"] == (
        "${{ steps.package-version.outputs.version }}"
    )
    version_step = next(
        step
        for step in publish_dev["steps"]
        if step.get("id") == "package-version"
    )
    script = version_step["run"]
    assert "uv run python - <<'PY'" in script
    assert 'Path("dist").glob("*.whl")' in script
    assert '.endswith(".dist-info/METADATA")' in script
    assert 'metadata.get("Version")' in script
    assert 'echo "version=$VERSION" >> "$GITHUB_OUTPUT"' in script


def test_verify_dev_consumes_publish_dev_artifact_version() -> None:
    verify_dev = _jobs()["verify-dev"]
    serialized = yaml.safe_dump(verify_dev)

    assert verify_dev["needs"] == "publish-dev"
    assert "${{ needs.publish-dev.outputs.version }}" in serialized
    assert "PACKAGE_VERSION" in serialized
    assert "setuptools_scm" not in serialized
    assert "Determine expected version" not in serialized
