"""Marketplace publishing contracts; no Azure credentials or uploads required."""

from __future__ import annotations

import base64
import importlib.util
import io
import json
import os
import shutil
import subprocess
from pathlib import Path
from urllib.error import HTTPError, URLError
from zipfile import ZipFile

import pytest
from ruamel.yaml import YAML

ROOT = Path(__file__).resolve().parents[2]
TENANT = "11111111-1111-1111-1111-111111111111"
PROFILE = "22222222-2222-2222-2222-222222222222"
OTHER = "33333333-3333-3333-3333-333333333333"
TOKEN = "dummy-access-token"


@pytest.fixture
def marketplace(monkeypatch):
    spec = importlib.util.spec_from_file_location("marketplace", ROOT / "scripts" / "marketplace.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv("MARKETPLACE_AZURE_TENANT_ID", TENANT)
    monkeypatch.setenv("MARKETPLACE_PROFILE_ID", PROFILE)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    return module


def assignment(role="Contributor", profile=PROFILE, deny=0):
    return {"identity": {"id": profile}, "role": {"name": role, "denyPermissions": deny}}


@pytest.mark.parametrize("role", ["Contributor", "Owner", "Creator"])
def test_requires_explicit_publishing_role(marketplace, role):
    assert marketplace.publishing_role(
        {"id": PROFILE}, {"value": [assignment(role)]}, PROFILE,
    ) == role


@pytest.mark.parametrize("values", [
    [], [assignment("Reader")], [assignment(profile=OTHER)],
    [assignment(deny=1)], [assignment(deny=None)], [assignment(deny="0")],
    [assignment(), assignment("Reader", deny=1)],
    [{}], ["invalid"], None,
])
def test_read_access_and_malformed_or_denied_roles_fail_closed(marketplace, values):
    with pytest.raises(marketplace.MarketplaceError):
        marketplace.publishing_role({"id": PROFILE}, {"value": values}, PROFILE)


def test_profile_mismatch_rejects_even_an_owner(marketplace):
    with pytest.raises(marketplace.MarketplaceError, match="identity mismatch"):
        marketplace.publishing_role(
            {"id": OTHER}, {"value": [assignment("Owner", OTHER)]}, PROFILE,
        )


@pytest.mark.parametrize("name", ["MARKETPLACE_AZURE_TENANT_ID", "MARKETPLACE_PROFILE_ID"])
@pytest.mark.parametrize("value", ["", "not-a-guid"])
def test_missing_configuration_fails_before_any_authentication(
    marketplace, monkeypatch, capsys, name, value,
):
    monkeypatch.setenv(name, value)
    monkeypatch.setattr(marketplace.subprocess, "run", lambda *a, **k: pytest.fail("must not run"))
    assert marketplace.main(["check"]) == 1
    assert name in capsys.readouterr().err


def test_child_environment_cannot_use_pat_or_environment_credentials(marketplace, monkeypatch):
    forbidden = [
        "VSCE_PAT", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET",
        "AZURE_CLIENT_CERTIFICATE_PATH", "AZURE_CLIENT_CERTIFICATE_PASSWORD",
        "AZURE_USERNAME", "AZURE_PASSWORD",
    ]
    for name in forbidden:
        monkeypatch.setenv(name, "unrelated-credential")
    monkeypatch.setenv("RELEASE_PAT", "github-credential")
    monkeypatch.setenv("AZURE_TENANT_ID", OTHER)
    env = marketplace.publishing_environment(TENANT)
    assert env["AZURE_TENANT_ID"] == TENANT
    assert env["RELEASE_PAT"] == "github-credential"
    assert all(name not in env for name in forbidden)
    assert marketplace.os.environ["VSCE_PAT"] == "unrelated-credential"


def mock_identity(marketplace, monkeypatch, *, role="Contributor", profile=PROFILE):
    requests = []
    monkeypatch.setattr(marketplace.shutil, "which", lambda name: f"/tools/{name}")

    def get_json(url, authorization):
        requests.append((url, authorization))
        if url == marketplace.PROFILE_URL:
            return {"id": profile}
        assert url == marketplace.ROLES_URL
        return {"value": [assignment(role, profile)]}

    monkeypatch.setattr(marketplace, "get_json", get_json)
    return requests


def test_read_only_preflight_matches_vsce_auth_without_upload(marketplace, monkeypatch, capsys):
    requests = mock_identity(marketplace, monkeypatch)
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        assert command == [
            "/tools/az", "account", "get-access-token", "--tenant", TENANT,
            "--resource", marketplace.RESOURCE, "--query", "accessToken",
            "--output", "tsv", "--only-show-errors",
        ]
        assert kwargs["capture_output"] is True
        assert kwargs["timeout"] == 60
        assert kwargs["env"]["AZURE_TENANT_ID"] == TENANT
        return subprocess.CompletedProcess(command, 0, TOKEN + "\n", "")

    monkeypatch.setattr(marketplace.subprocess, "run", run)
    assert marketplace.main(["check"]) == 0
    assert len(calls) == 1
    basic = base64.b64encode(f"OAuth:{TOKEN}".encode()).decode()
    assert requests == [
        (marketplace.PROFILE_URL, "Bearer " + TOKEN),
        (marketplace.ROLES_URL, f"Basic {basic}"),
    ]
    output = capsys.readouterr().out
    assert TOKEN not in output and basic not in output
    assert "explicit Contributor" in output and "No upload" in output


@pytest.mark.parametrize("failure", ["exit", "empty", "timeout", "oserror"])
def test_az_errors_do_not_print_credentials(marketplace, monkeypatch, capsys, failure):
    monkeypatch.setattr(marketplace.shutil, "which", lambda name: name)

    def run(command, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 60, output=TOKEN, stderr=TOKEN)
        if failure == "oserror":
            raise OSError(TOKEN)
        return subprocess.CompletedProcess(command, 1 if failure == "exit" else 0, "", TOKEN)

    monkeypatch.setattr(marketplace.subprocess, "run", run)
    assert marketplace.main(["check"]) == 1
    output = capsys.readouterr()
    assert TOKEN not in output.out + output.err
    assert "ERROR:" in output.err


@pytest.mark.parametrize("failure", [
    HTTPError("https://example.invalid", 403, TOKEN, {}, None),
    HTTPError("https://example.invalid", 302, TOKEN, {}, None),
    URLError(TOKEN), TimeoutError(TOKEN),
])
def test_http_failures_are_safe(marketplace, monkeypatch, failure):
    class Opener:
        def open(self, request, timeout):
            assert timeout == 30
            raise failure

    monkeypatch.setattr(marketplace, "build_opener", lambda *a: Opener())
    with pytest.raises(marketplace.MarketplaceError) as error:
        marketplace.get_json(marketplace.PROFILE_URL, f"Basic {TOKEN}")
    assert TOKEN not in str(error.value)


@pytest.mark.parametrize("body", [b"not json", b"[]", b"null", b"\xff"])
def test_malformed_http_payload_fails(marketplace, monkeypatch, body):
    class Opener:
        def open(self, request, timeout):
            return io.BytesIO(body)

    monkeypatch.setattr(marketplace, "build_opener", lambda *a: Opener())
    with pytest.raises(marketplace.MarketplaceError):
        marketplace.get_json(marketplace.PROFILE_URL, "Basic dummy")


def test_authorization_is_never_redirected(marketplace):
    assert marketplace.NoRedirect().redirect_request(
        None, None, 302, "", {}, "https://other.invalid",
    ) is None


@pytest.fixture
def vsix(tmp_path):
    path = tmp_path / "extension with spaces.vsix"
    with ZipFile(path, "w") as archive:
        archive.writestr("extension/package.json", json.dumps({
            "publisher": "AgentOpsAccelerator", "name": "agentops-accelerator", "version": "1.2.3",
        }))
    return path


@pytest.mark.parametrize("pre_release", [True, False])
@pytest.mark.parametrize("allow_duplicate", [True, False])
def test_publish_uses_entra_and_native_duplicate_handling(
    marketplace, monkeypatch, capsys, vsix, pre_release, allow_duplicate,
):
    mock_identity(marketplace, monkeypatch)
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        assert "VSCE_PAT" not in kwargs["env"]
        if command[1] == "--version":
            return subprocess.CompletedProcess(command, 0, "3.9.2\n", "")
        if command[1] == "account":
            return subprocess.CompletedProcess(command, 0, TOKEN, "")
        basic = base64.b64encode(f"OAuth:{TOKEN}".encode()).decode()
        return subprocess.CompletedProcess(command, 0, f"Published {TOKEN} {basic}\n")

    monkeypatch.setattr(marketplace.subprocess, "run", run)
    args = ["publish", "--package-path", str(vsix)]
    if pre_release:
        args.append("--pre-release")
    if allow_duplicate:
        args.append("--allow-already-exists")
    assert marketplace.main(args) == 0
    assert [cmd[1] for cmd in calls] == ["--version", "account", "publish"]
    expected = ["/tools/vsce", "publish", "--azure-credential", "--packagePath", str(vsix)]
    if pre_release:
        expected.append("--pre-release")
    if allow_duplicate:
        expected.append("--skip-duplicate")
    assert calls[-1] == expected
    output = capsys.readouterr().out
    assert TOKEN not in output
    assert "***" in output


@pytest.mark.parametrize("role,profile", [("Reader", PROFILE), ("Owner", OTHER)])
def test_failed_preflight_never_reaches_publish(marketplace, monkeypatch, vsix, role, profile):
    mock_identity(marketplace, monkeypatch, role=role, profile=profile)

    def run(command, **kwargs):
        assert command[1] != "publish"
        return subprocess.CompletedProcess(
            command, 0, "3.9.2" if command[1] == "--version" else TOKEN, "",
        )

    monkeypatch.setattr(marketplace.subprocess, "run", run)
    assert marketplace.main(["publish", "--package-path", str(vsix)]) == 1


def test_publish_error_with_already_exists_text_is_not_swallowed(marketplace, monkeypatch, vsix):
    mock_identity(marketplace, monkeypatch)

    def run(command, **kwargs):
        if command[1] == "--version":
            return subprocess.CompletedProcess(command, 0, "3.9.2", "")
        if command[1] == "account":
            return subprocess.CompletedProcess(command, 0, TOKEN, "")
        return subprocess.CompletedProcess(command, 7, "Auth failed: identity already exists\n")

    monkeypatch.setattr(marketplace.subprocess, "run", run)
    assert marketplace.main([
        "publish", "--package-path", str(vsix), "--allow-already-exists",
    ]) == 7


@pytest.mark.parametrize("version", ["2.26.0", "3.9.1", "garbage", "3.9.2-dev.1"])
def test_old_or_unknown_vsce_rejected(marketplace, monkeypatch, vsix, version):
    monkeypatch.setattr(marketplace.shutil, "which", lambda name: name)
    monkeypatch.setattr(
        marketplace.subprocess, "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, version, ""),
    )
    monkeypatch.setattr(marketplace, "preflight", lambda *a: pytest.fail("must fail before auth"))
    assert marketplace.main(["publish", "--package-path", str(vsix)]) == 1


@pytest.mark.parametrize("manifest", [
    {}, {"publisher": "other", "name": "agentops-accelerator"},
    {"publisher": "AgentOpsAccelerator", "name": "other"}, [],
])
def test_wrong_extension_never_authenticates(marketplace, monkeypatch, vsix, manifest):
    with ZipFile(vsix, "w") as archive:
        archive.writestr("extension/package.json", json.dumps(manifest))
    monkeypatch.setattr(marketplace.subprocess, "run", lambda *a, **k: pytest.fail("must not run"))
    assert marketplace.main(["publish", "--package-path", str(vsix)]) == 1


def load_workflow(name):
    return YAML(typ="safe").load((ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("file,job_name,environment,pre_release", [
    ("staging.yml", "publish-vsix-prerelease", "marketplace-staging", True),
    ("release.yml", "publish-vsix", "marketplace-release", False),
])
def test_workflows_use_dedicated_oidc_environments(file, job_name, environment, pre_release):
    job = load_workflow(file)["jobs"][job_name]
    assert job["environment"] == environment
    assert job["permissions"] == {"contents": "read", "id-token": "write"}
    assert job["if"]
    assert job["env"]["MARKETPLACE_PROFILE_ID"] == "${{ vars.MARKETPLACE_PROFILE_ID }}"
    steps = job["steps"]
    login = next(step for step in steps if step.get("uses") == "./.github/actions/marketplace-login")
    assert login["with"] == {
        "client-id": "${{ vars.MARKETPLACE_AZURE_CLIENT_ID }}",
        "tenant-id": "${{ vars.MARKETPLACE_AZURE_TENANT_ID }}",
        "profile-id": "${{ vars.MARKETPLACE_PROFILE_ID }}",
    }
    assert any(step.get("run") == "npm install -g @vscode/vsce@3.9.2" for step in steps)
    publish = next(step for step in steps if "python scripts/marketplace.py publish" in step.get("run", ""))
    assert ("--pre-release" in publish["run"]) == pre_release
    assert "--allow-already-exists" in publish["run"]
    assert steps.index(login) < steps.index(publish)
    assert "VSCE_PAT" not in json.dumps(job)
    assert "continue-on-error" not in json.dumps(job)


def test_login_does_not_require_subscription_or_shared_e2e_identity():
    action = YAML(typ="safe").load(
        (ROOT / ".github" / "actions" / "marketplace-login" / "action.yml").read_text(),
    )
    steps = action["runs"]["steps"]
    assert "PROFILE_ID" in steps[0]["run"]
    login = steps[1]
    assert login["uses"] == "azure/login@v3"
    assert login["with"]["allow-no-subscriptions"] is True
    assert "subscription-id" not in login["with"]


def test_release_retry_keeps_tooling_separate_from_older_release_sources():
    steps = load_workflow("release.yml")["jobs"]["publish-vsix"]["steps"]
    checkouts = [step for step in steps if step.get("uses") == "actions/checkout@v7"]
    assert checkouts[0]["with"] == {"ref": "${{ github.workflow_sha }}"}
    assert checkouts[1]["with"]["ref"] == "refs/tags/${{ inputs.tag || github.ref_name }}"
    assert checkouts[1]["with"]["path"] == "release-source"
    for name in ("Sync VSIX version from git tag", "Copy root assets for VSIX"):
        assert next(step for step in steps if step.get("name") == name)["working-directory"] == "release-source"
    package = next(step for step in steps if step.get("name") == "Package VSIX")
    assert package["working-directory"] == "release-source/plugins/agentops"
    artifact = next(step for step in steps if step.get("uses") == "actions/upload-artifact@v7")
    assert artifact["with"]["path"] == "release-source/plugins/agentops/${{ env.VSIX_FILE }}"


def test_python_publishing_and_github_release_contracts_stay_intact():
    staging = load_workflow("staging.yml")["jobs"]["publish-testpypi"]
    release = load_workflow("release.yml")["jobs"]
    for job, environment in [
        (staging, "staging"), (release["publish-testpypi"], "staging"),
        (release["publish-pypi"], "release"),
    ]:
        assert job["environment"] == environment
        assert job["permissions"]["id-token"] == "write"
        assert any(step.get("uses") == "pypa/gh-action-pypi-publish@release/v1" for step in job["steps"])
    assert release["publish-vsix"]["needs"] == ["build", "publish-pypi"]
    assert release["github-release"]["needs"] == ["publish-pypi", "publish-vsix"]
    cut_release = (ROOT / ".github" / "workflows" / "cut-release.yml").read_text(encoding="utf-8")
    assert "secrets.RELEASE_PAT" in cut_release


@pytest.mark.parametrize("name", ["release.ps1", "release.sh", "staging.ps1", "staging.sh"])
def test_local_scripts_preflight_before_release_actions(name):
    text = (ROOT / "scripts" / name).read_text(encoding="utf-8")
    assert "VSCE_PAT" not in text
    assert "marketplace.py" in text
    assert " publish --" in text
    assert text.index(" check") < text.index("uv build")
    if name.startswith("release."):
        assert text.index(" check") < text.index('git push origin')
    if name.endswith(".ps1"):
        assert 'throw "Marketplace' in text
        assert "finally {" in text


def test_npm_publishing_also_uses_shared_permission_preflight():
    scripts = json.loads((ROOT / "plugins" / "agentops" / "package.json").read_text())["scripts"]
    for name in ("publish", "publish:prerelease"):
        assert "scripts/marketplace.py publish" in scripts[name]
        assert "vsce publish" not in scripts[name]
    assert scripts["package"] == "vsce package"
    assert scripts["package:prerelease"] == "vsce package --pre-release"


@pytest.mark.parametrize("ref,tag,expected", [
    ("refs/tags/v1.2.3", "v1.2.3", 0),
    ("refs/heads/main", "v1.2.3", 0),
    ("refs/heads/main", "main", 1),
    ("refs/tags/v1.2.3", "v4.5.6", 1),
    ("refs/tags/v1.2.3", "v1.2.3-rc1", 1),
    ("refs/heads/main", "v1.2.3; echo unexpected", 1),
])
def test_stable_release_ref_guard(ref, tag, expected):
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("Bash is not installed")
    step = load_workflow("release.yml")["jobs"]["publish-vsix"]["steps"][0]
    assert step["name"] == "Validate Marketplace release ref"
    result = subprocess.run(
        [bash, "-c", step["run"]],
        env={**os.environ, "GITHUB_REF": ref, "GITHUB_REF_NAME": ref.rsplit("/", 1)[-1], "RELEASE_TAG": tag},
        capture_output=True, text=True, timeout=15, check=False,
    )
    assert result.returncode == expected
    assert "unexpected" not in result.stdout


@pytest.mark.parametrize("client,tenant,profile,expected", [
    (OTHER, TENANT, PROFILE, 0), ("", TENANT, PROFILE, 1),
    (OTHER, "", PROFILE, 1), (OTHER, TENANT, "", 1),
    ("$(echo unexpected)", TENANT, PROFILE, 1),
])
def test_login_configuration_guard(client, tenant, profile, expected):
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("Bash is not installed")
    action = YAML(typ="safe").load(
        (ROOT / ".github" / "actions" / "marketplace-login" / "action.yml").read_text(),
    )
    script = action["runs"]["steps"][0]["run"]
    result = subprocess.run(
        [bash, "-c", script],
        env={**os.environ, "CLIENT_ID": client, "TENANT_ID": tenant, "PROFILE_ID": profile},
        capture_output=True, text=True, timeout=15, check=False,
    )
    assert result.returncode == expected
    assert "unexpected" not in result.stdout


def test_github_masks_escape_workflow_command_delimiters(marketplace, monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    marketplace.mask("dummy%\r\nsecret")
    assert capsys.readouterr().out == "::add-mask::dummy%25%0D%0Asecret\n"


def test_discovery_bootstraps_without_profile_or_publisher_access(marketplace, monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("MARKETPLACE_PROFILE_ID", raising=False)
    monkeypatch.setattr(marketplace, "get_token", lambda *a: TOKEN)
    requests = []

    def get_json(url, authorization):
        requests.append(url)
        assert url == marketplace.PROFILE_URL
        return {"id": PROFILE, "emailAddress": "not-for-artifacts@example.invalid"}

    monkeypatch.setattr(marketplace, "get_json", get_json)
    output = tmp_path / "profile.json"
    assert marketplace.main(["discover", "--out", str(output)]) == 0
    assert json.loads(output.read_text()) == {"tenant_id": TENANT, "profile_id": PROFILE}
    assert requests == [marketplace.PROFILE_URL]
    assert TOKEN not in output.read_text() + capsys.readouterr().out


@pytest.mark.parametrize("profile", [{}, {"id": "invalid"}, {"id": None}, {"id": 42}])
def test_discovery_rejects_invalid_profile(marketplace, monkeypatch, profile):
    monkeypatch.setattr(marketplace, "get_token", lambda *a: TOKEN)
    monkeypatch.setattr(marketplace, "get_json", lambda *a: profile)
    assert marketplace.main(["discover"]) == 1


def test_preflight_workflow_has_no_upload_or_mutation_commands():
    workflow = load_workflow("marketplace-preflight.yml")
    assert set(workflow["on"]) == {"workflow_dispatch", "workflow_call"}
    modes = workflow["on"]["workflow_dispatch"]["inputs"]["mode"]["options"]
    assert modes == ["discover", "check"]
    job = workflow["jobs"]["preflight"]
    assert job["permissions"] == {"contents": "read", "id-token": "write"}
    assert job["environment"] == "${{ inputs.environment }}"
    steps = job["steps"]
    runs = "\n".join(step.get("run", "") for step in steps)
    for forbidden in ("vsce ", "marketplace.py publish", "gh release", "az role", "curl ", "git push"):
        assert forbidden not in runs
    assert "python scripts/marketplace.py discover --out marketplace-profile.json" in runs
    assert "python scripts/marketplace.py check" in runs
    artifact = next(step for step in steps if step.get("uses") == "actions/upload-artifact@v7")
    assert artifact["with"]["path"] == "marketplace-profile.json"
    assert artifact["if"] == "inputs.mode == 'discover'"
    login = next(step for step in steps if step.get("uses") == "./.github/actions/marketplace-login")
    assert login["with"]["discover-profile"] == "${{ inputs.mode == 'discover' && 'true' || 'false' }}"


@pytest.mark.parametrize("mode,expected", [("true", 0), ("false", 1), ("wrong", 1)])
def test_profile_bypass_is_explicit_and_only_for_discovery(mode, expected):
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("Bash is not installed")
    action = YAML(typ="safe").load(
        (ROOT / ".github" / "actions" / "marketplace-login" / "action.yml").read_text(),
    )
    result = subprocess.run(
        [bash, "-c", action["runs"]["steps"][0]["run"]],
        env={**os.environ, "CLIENT_ID": OTHER, "TENANT_ID": TENANT, "PROFILE_ID": "", "DISCOVER_PROFILE": mode},
        capture_output=True, text=True, timeout=15, check=False,
    )
    assert result.returncode == expected
