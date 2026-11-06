"""Offline trust-boundary tests for generated PR evaluation inputs."""

import json
import subprocess
from pathlib import Path

import pytest

from agentops.services import pr_gate
from agentops.utils.yaml import load_yaml, save_yaml


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True,
    ).stdout.strip()


@pytest.fixture
def repository(tmp_path, monkeypatch):
    for key in ("AGENTOPS_AGENT_PROMPT_FILE", "AGENTOPS_PR_PROMPT_CANDIDATE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", "https://project.internal.example")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://judge.internal.example")
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "config", "user.name", "Test")
    save_yaml(tmp_path / "agentops.yaml", {
        "version": 1,
        "agent": "https://agent.internal.example/chat",
        "protocol": "http-json",
        "dataset": "data.jsonl",
        "prompt_file": "instructions.md",
        "auth_header_env": "DUMMY_TOKEN",
        "project_endpoint": "https://project.internal.example",
    })
    (tmp_path / "data.jsonl").write_text('{"input":"base"}\n')
    (tmp_path / "instructions.md").write_text("base instructions")
    (tmp_path / ".env").write_text("DUMMY=base\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "Trusted base")
    return tmp_path, _git(tmp_path, "rev-parse", "HEAD")


def _candidate(repository, **changes):
    root, base = repository
    config = load_yaml(root / "agentops.yaml")
    config.update(changes)
    save_yaml(root / "agentops.yaml", config)
    (root / "data.jsonl").write_text('{"input":"candidate","expected":"dummy"}\n')
    (root / "instructions.md").write_text("candidate instructions")
    (root / ".env").write_text("DUMMY=candidate\n")
    (root / "sitecustomize.py").write_text("raise RuntimeError('must never execute')")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "Untrusted candidate")
    sha = _git(root, "rev-parse", "HEAD")
    _git(root, "checkout", "--detach", base)
    return sha


def test_only_data_is_imported_and_config_destinations_stay_trusted(repository):
    root, base = repository
    sha = _candidate(repository)
    pr_gate.prepare_inputs(root, candidate_sha=sha)
    config = load_yaml(root / "agentops.yaml")
    assert _git(root, "rev-parse", "HEAD") == base
    assert config["agent"] == "https://agent.internal.example/chat"
    assert config["auth_header_env"] == "DUMMY_TOKEN"
    assert config["project_endpoint"] == "https://project.internal.example"
    assert json.loads((root / "data.jsonl").read_text())["input"] == "candidate"
    assert (root / config["prompt_file"]).read_text() == "candidate instructions"
    assert (root / "instructions.md").read_text() == "base instructions"
    assert (root / ".env").read_text() == "DUMMY=base\n"
    assert not (root / "sitecustomize.py").exists()


@pytest.mark.parametrize("changes", [
    {"agent": "https://receiver.invalid/.services.ai.azure.com/agents/bot"},
    {"project_endpoint": "https://receiver.invalid"},
    {"auth_header_env": "OTHER_DUMMY_TOKEN"},
    {"auth_header_name": "X-API-KEY"},
    {"auth_value_template": "{token}"},
    {"headers": {"X-Secret": "dummy"}},
    {"execution": "azd"},
    {"assert": {"config": "evil.yaml", "env": {"AZURE_API_BASE": "https://receiver.invalid"}}},
    {"dataset": "different.jsonl"},
    {"prompt_agent_bootstrap": {"model": "other", "tools": []}},
])
def test_candidate_cannot_change_policy(repository, changes):
    root, _ = repository
    sha = _candidate(repository, **changes)
    with pytest.raises(ValueError, match="trusted-base configuration"):
        pr_gate.prepare_inputs(root, candidate_sha=sha)
    assert json.loads((root / "data.jsonl").read_text())["input"] == "base"


@pytest.mark.parametrize("path", ["../outside.jsonl", "/tmp/input", "C:\\input", ".env\nINJECT=1"])
def test_repository_paths_are_not_escapes(path):
    with pytest.raises(ValueError, match="relative repository"):
        pr_gate._repo_path(path)


def test_git_symlink_is_rejected_without_following_it(repository):
    root, _base = repository
    oid = _git(root, "hash-object", "-w", "instructions.md")
    _git(root, "update-index", "--add", "--cacheinfo", f"120000,{oid},data.jsonl")
    _git(root, "commit", "-m", "Candidate Git symlink")
    sha = _git(root, "rev-parse", "HEAD")
    with pytest.raises(ValueError, match="regular Git file"):
        pr_gate.prepare_inputs(root, candidate_sha=sha)


def test_size_is_checked_before_blob_contents(repository, monkeypatch):
    root, _ = repository
    sha = _candidate(repository)
    monkeypatch.setattr(pr_gate, "_MAX_DATASET", 1)
    with pytest.raises(ValueError, match="size limit"):
        pr_gate.prepare_inputs(root, candidate_sha=sha)


def test_malformed_dataset_does_not_partially_overlay_inputs(repository):
    root, base = repository
    (root / "data.jsonl").write_text('not json\n')
    _git(root, "commit", "-am", "Malformed candidate data")
    sha = _git(root, "rev-parse", "HEAD")
    _git(root, "checkout", "--detach", base)
    with pytest.raises(ValueError):
        pr_gate.prepare_inputs(root, candidate_sha=sha)
    assert (root / "data.jsonl").read_text() == '{"input":"base"}\n'


def test_trusted_remote_dataset_reference_is_not_a_candidate_download(repository):
    root, _ = repository
    config = load_yaml(root / "agentops.yaml")
    config["dataset"] = "https://data.blob.core.windows.net/evals/data.jsonl"
    save_yaml(root / "agentops.yaml", config)
    _git(root, "commit", "-am", "Approve remote dataset")
    repository = root, _git(root, "rev-parse", "HEAD")
    sha = _candidate(repository)
    pr_gate.prepare_inputs(root, candidate_sha=sha)
    assert load_yaml(root / "agentops.yaml")["dataset"] == config["dataset"]
    assert (root / "data.jsonl").read_text() == '{"input":"base"}\n'


def test_credential_destinations_require_https_in_pr_gate(repository):
    root, _ = repository
    config = load_yaml(root / "agentops.yaml")
    config["project_endpoint"] = "http://project.internal.example"
    save_yaml(root / "agentops.yaml", config)
    _git(root, "commit", "-am", "Invalid CI transport policy")
    repository = root, _git(root, "rev-parse", "HEAD")
    sha = _candidate(repository)
    with pytest.raises(ValueError, match="require HTTPS"):
        pr_gate.prepare_inputs(root, candidate_sha=sha)


def test_ado_refuses_non_pr_ref_before_fetch(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTOPS_PR_SHA", "a" * 40)
    monkeypatch.setenv("AGENTOPS_PR_SOURCE_REF", "refs/heads/untrusted")
    monkeypatch.setattr(pr_gate, "_git", lambda *a, **k: pytest.fail("must not fetch"))
    with pytest.raises(ValueError, match="PR merge ref"):
        pr_gate.prepare_from_environment(tmp_path)


def test_moving_candidate_is_rejected_before_data_or_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTOPS_PR_SHA", "a" * 40)
    monkeypatch.delenv("AGENTOPS_PR_SOURCE_REF", raising=False)
    monkeypatch.setattr(pr_gate, "_git", lambda root, command, *args, **kw: (
        b"" if command == "fetch" else ("b" * 40).encode()
    ))
    monkeypatch.setattr(pr_gate, "prepare_inputs", lambda *a, **k: pytest.fail("must not prepare"))
    with pytest.raises(ValueError, match="revision changed"):
        pr_gate.prepare_from_environment(tmp_path)


def test_ado_pins_git_destination_and_keeps_protected_checkout(tmp_path, monkeypatch, capsys):
    merge, target, source = "a" * 40, "b" * 40, "c" * 40
    approved = "https://dev.azure.com/example/project/_git/repository"
    for key, value in {
        "AGENTOPS_PR_SHA": merge,
        "AGENTOPS_PR_SOURCE_REF": "refs/pull/42/merge",
        "AGENTOPS_PR_GIT_URL": approved,
        "AGENTOPS_PR_GIT_TOKEN": "dummy-read-token",
        "BUILD_REPOSITORY_URI": "https://untrusted.invalid/repository",
    }.items():
        monkeypatch.setenv(key, value)
    calls = []

    def git(root, command, *args, **kwargs):
        calls.append((command, args))
        if command == "rev-parse":
            return merge.encode()
        if command == "rev-list":
            return f"{merge} {target} {source}".encode()
        assert command == "fetch"
        return b""

    prepared = []
    monkeypatch.setattr(pr_gate, "_git", git)
    monkeypatch.setattr(pr_gate, "prepare_inputs", lambda *args, **kwargs: prepared.append(kwargs))
    pr_gate.prepare_from_environment(tmp_path)
    assert approved in calls[0][1]
    assert not any(command == "checkout" for command, _ in calls)
    assert prepared[0]["candidate_sha"] == merge
    assert f"AGENTOPS_PR_SHA;isReadOnly=true]{source}" in capsys.readouterr().out


def test_error_diagnostics_cannot_inject_ado_logging_commands(monkeypatch, capsys):
    monkeypatch.setattr(pr_gate.sys, "argv", ["pr_gate"])

    def fail(root):
        raise ValueError("invalid YAML\n##vso[task.setvariable variable=AGENTOPS_PR_PREPARED]true")

    monkeypatch.setattr(pr_gate, "prepare_from_environment", fail)
    with pytest.raises(SystemExit) as error:
        pr_gate.main()
    assert error.value.code == 1
    output = capsys.readouterr().err
    assert "##vso[" not in output
    assert len(output.splitlines()) == 1


def test_trusted_dispatch_stages_prompt_without_pr_fetch(repository, monkeypatch):
    root, base = repository
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    monkeypatch.delenv("AGENTOPS_PR_SHA", raising=False)
    pr_gate.prepare_from_environment(root)
    config = load_yaml(root / "agentops.yaml")
    assert (root / config["prompt_file"]).read_text() == "base instructions"
    assert _git(root, "rev-parse", "HEAD") == base


def test_ado_status_is_bound_to_iteration_and_never_approves_newer_head(tmp_path, monkeypatch):
    directory = tmp_path / ".agentops/pr-input"
    directory.mkdir(parents=True)
    (directory / "revision.json").write_text(json.dumps({"source_sha": "a" * 40, "pr": 42}))
    for key, value in {
        "AGENTOPS_ADO_COLLECTION_URI": "https://dev.azure.com/example/",
        "AGENTOPS_ADO_PROJECT_ID": "project-id",
        "AGENTOPS_ADO_REPOSITORY_ID": "source-id",
        "SYSTEM_COLLECTIONURI": "https://untrusted.invalid/",
        "BUILD_REPOSITORY_ID": "untrusted-repository",
        "BUILD_BUILDID": "123",
        "AGENT_JOBSTATUS": "Succeeded",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(pr_gate, "_ado_status_token", lambda: "dummy-exclusive-principal-token")
    captured = []
    current_sha = "b" * 40

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({"value": [{
                "id": 3, "sourceRefCommit": {"commitId": current_sha},
            }]}).encode()

    class Opener:
        def open(self, request, timeout):
            captured.append(request)
            return Response()

    monkeypatch.setattr(pr_gate.urllib.request, "build_opener", lambda *args: Opener())
    with pytest.raises(ValueError, match="refusing to approve a newer revision"):
        pr_gate.publish_ado_status(tmp_path)
    assert len(captured) == 1
    assert captured[0].full_url.startswith("https://dev.azure.com/example/project-id/")
    assert captured[0].get_header("Authorization") == "Bearer dummy-exclusive-principal-token"
    current_sha = "a" * 40
    captured.clear()
    pr_gate.publish_ado_status(tmp_path)
    status = json.loads(captured[-1].data)
    assert status["state"] == "succeeded"
    assert status["iterationId"] == 3
    assert status["context"] == {"name": "pr-eval", "genre": "agentops"}
    monkeypatch.setenv("AGENT_JOBSTATUS", "Failed")
    captured.clear()
    pr_gate.publish_ado_status(tmp_path)
    assert json.loads(captured[-1].data)["state"] == "failed"
