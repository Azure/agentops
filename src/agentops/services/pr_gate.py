"""Internal, data-only PR input preparation for trusted generated pipelines.

Run the installed package with ``python -I -m agentops.services.pr_gate``.
The workflow definition and initial checkout must be trusted. A candidate is
read as Git blobs, never checked out, installed, imported, or executed.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from agentops.core.agentops_config import AgentOpsConfig, is_unexpanded_ci_token
from agentops.core.dataset_source import parse_dataset_reference
from agentops.utils.yaml import load_yaml, save_yaml

_SHA = re.compile(r"[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?")
_MERGE_REF = re.compile(r"refs/pull/([1-9][0-9]*)/merge")
_INPUT_DIR = Path(".agentops/pr-input")
_MAX_CONFIG = 1024 * 1024
_MAX_DATASET = 100 * 1024 * 1024


def _git(root: Path, *args: str, token: str = "") -> bytes:
    env = os.environ.copy()
    if token:
        # Do not put the read token in command arguments or persist it in Git.
        remote = env.get("AGENTOPS_PR_GIT_URL") or _git(
            root, "remote", "get-url", "origin"
        ).decode().strip()
        _validate_https(remote)
        env.update(
            GIT_CONFIG_COUNT="2",
            GIT_CONFIG_KEY_0=f"http.{remote}.extraheader",
            GIT_CONFIG_VALUE_0=f"AUTHORIZATION: bearer {token}",
            GIT_CONFIG_KEY_1="http.followRedirects",
            GIT_CONFIG_VALUE_1="false",
        )
    result = subprocess.run(
        ["git", *args], cwd=root, env=env, capture_output=True, check=False,
        timeout=120,
    )
    if result.returncode:
        # Git/server errors may contain credential-bearing remote URLs.
        raise ValueError(f"PR input Git operation failed: {args[0]}")
    return result.stdout


def _repo_path(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value or path.is_absolute() or "\\" in value or ":" in value
        or any(ord(char) < 32 for char in value)
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise ValueError("PR input paths must be relative repository file paths")
    return path.as_posix()


def _blob(root: Path, sha: str, path: str, limit: int) -> bytes:
    path = _repo_path(path)
    listing = _git(root, "ls-tree", "-z", sha, "--", path)
    entries = [entry for entry in listing.split(b"\0") if entry]
    if len(entries) != 1:
        raise ValueError(f"PR input must identify one regular Git file: {path}")
    metadata, actual_path = entries[0].split(b"\t", 1)
    mode, kind, oid = metadata.decode("ascii").split()
    if mode not in {"100644", "100755"} or kind != "blob" or actual_path.decode() != path:
        raise ValueError(f"PR input is not a regular Git file: {path}")
    object_id = oid.strip()
    if int(_git(root, "cat-file", "-s", object_id)) > limit:
        raise ValueError(f"PR input exceeds its size limit: {path}")
    return _git(root, "cat-file", "blob", object_id)


def _workspace_file(root: Path, value: str) -> Path:
    path = root / _repo_path(value)
    if not path.resolve().is_relative_to(root.resolve()) or path.is_symlink():
        raise ValueError("PR input path escapes the trusted workspace")
    return path


def _validate_https(value: str) -> None:
    parts = urllib.parse.urlsplit(value)
    if (
        parts.scheme != "https" or not parts.hostname
        or parts.username is not None or parts.password is not None
    ):
        raise ValueError("Credential-bearing PR evaluation destinations require HTTPS")


def prepare_inputs(
    root: Path, *, candidate_sha: str, config_path: str = "agentops.yaml",
) -> None:
    """Overlay only validated dataset/prompt data onto the trusted workspace."""
    if not _SHA.fullmatch(candidate_sha):
        raise ValueError("PR input requires an immutable candidate commit SHA")
    root = root.resolve()
    config_path = _repo_path(config_path)
    source = _workspace_file(root, config_path)
    trusted = load_yaml(source)
    proposed = YAML(typ="safe").load(
        _blob(root, candidate_sha, config_path, _MAX_CONFIG).decode("utf-8")
    )
    if not isinstance(proposed, dict) or not all(
        isinstance(key, str) and re.fullmatch(r"[a-z][a-z0-9_]*", key)
        for key in proposed
    ):
        raise ValueError("PR agentops.yaml must be a mapping")
    changed = sorted(
        key for key in trusted.keys() | proposed.keys()
        if trusted.get(key) != proposed.get(key)
    )
    if changed:
        raise ValueError(
            "PR evaluation uses trusted-base configuration. Merge configuration "
            "changes through review before using them in this gate. Changed fields: "
            + ", ".join(str(key) for key in changed)
        )
    config = AgentOpsConfig.model_validate(trusted)
    for endpoint in (
        config.project_endpoint,
        os.environ.get("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT"),
        os.environ.get("AZURE_OPENAI_ENDPOINT"),
    ):
        if endpoint:
            _validate_https(endpoint)
    if (
        config.agent and config.agent.lower().startswith(("http://", "https://"))
        and (config.auth_header_env or config.headers or config.resolved_target().kind == "foundry_hosted")
    ):
        _validate_https(config.agent)
    assert_config = trusted.get("assert")
    if isinstance(assert_config, dict):
        extra_env = assert_config.get("env") or {}
        if isinstance(extra_env, dict) and extra_env.get("AZURE_API_BASE"):
            _validate_https(str(extra_env["AZURE_API_BASE"]))

    reference = parse_dataset_reference(config.dataset)
    pending: list[tuple[Path, bytes]] = []
    if not reference.source_uri:
        relative = PurePosixPath(config_path).parent / str(config.dataset).replace("\\", "/")
        dataset = _workspace_file(root, relative.as_posix())
        if dataset.suffix.lower() != ".jsonl":
            raise ValueError("PR candidate datasets must be JSONL data files")
        payload = _blob(root, candidate_sha, relative.as_posix(), _MAX_DATASET)
        for line in payload.decode("utf-8").splitlines():
            if line.strip() and not isinstance(json.loads(line), dict):
                raise ValueError("Each PR dataset row must be a JSON object")
        pending.append((dataset, payload))

    prompt = os.environ.get("AGENTOPS_AGENT_PROMPT_FILE") or trusted.get("prompt_file")
    if prompt and is_unexpanded_ci_token(str(prompt)):
        prompt = trusted.get("prompt_file")
    if not prompt and os.environ.get("AGENTOPS_PR_PROMPT_CANDIDATE") == "true":
        prompt = ".agentops/prompts/agent-instructions.md"
    if prompt:
        relative = PurePosixPath(config_path).parent / str(prompt).replace("\\", "/")
        payload = _blob(root, candidate_sha, relative.as_posix(), _MAX_CONFIG)
        if not payload.decode("utf-8").strip():
            raise ValueError("PR prompt input must not be empty")
        destination = _workspace_file(root, (_INPUT_DIR / "prompt.txt").as_posix())
        pending.append((destination, payload))
        trusted["prompt_file"] = os.path.relpath(destination, source.parent)

    # Validate everything before writing. Never overlay scripts or config from
    # the candidate, including .env, recipes, ASSERT config, or bootstrap tools.
    for path, payload in pending:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    save_yaml(source, trusted)


def prepare_from_environment(root: Path) -> None:
    """Fetch one immutable candidate using trusted CI event metadata."""
    env = os.environ
    sha = env.get("AGENTOPS_PR_SHA", "")
    if not sha:
        if env.get("GITHUB_EVENT_NAME") == "workflow_dispatch":
            # A dispatch evaluates its selected trusted revision, not a PR.
            prepare_inputs(
                root, candidate_sha=_git(root, "rev-parse", "HEAD").decode().strip(),
                config_path=env.get("AGENTOPS_PR_CONFIG", "agentops.yaml"),
            )
            return
        raise ValueError("Missing immutable PR candidate metadata")
    if not _SHA.fullmatch(sha):
        raise ValueError("Invalid immutable PR candidate commit SHA")
    source_ref = env.get("AGENTOPS_PR_SOURCE_REF", "")
    match = None
    if source_ref:
        match = _MERGE_REF.fullmatch(source_ref)
        if match is None:
            raise ValueError("The ADO input pipeline must validate a PR merge ref")
    token = env.get("AGENTOPS_PR_GIT_TOKEN", "")
    remote = "origin"
    if source_ref:
        remote = env["AGENTOPS_PR_GIT_URL"]
        _validate_https(remote)
    _git(root, "fetch", "--no-tags", "--no-recurse-submodules", "--depth=2",
         remote, source_ref or sha, token=token)
    fetched = _git(root, "rev-parse", "FETCH_HEAD").decode().strip()
    if fetched.lower() != sha.lower():
        raise ValueError("PR revision changed during preparation, queue a fresh evaluation")
    source_sha = sha
    if match is not None:
        parents = _git(root, "rev-list", "--parents", "-n", "1", sha).decode().split()
        if len(parents) != 3:
            raise ValueError("ADO PR input must be the server-generated merge commit")
        # Keep executing the explicitly protected checkout, not a target branch
        # selected through PR-authored pipeline metadata.
        record = {"source_sha": parents[2], "pr": int(match.group(1))}
        source_sha = parents[2]
        target = _workspace_file(root, (_INPUT_DIR / "revision.json").as_posix())
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(record), encoding="utf-8")
    prepare_inputs(
        root, candidate_sha=sha,
        config_path=env.get("AGENTOPS_PR_CONFIG", "agentops.yaml"),
    )
    if match is not None:
        print("##vso[task.setvariable variable=AGENTOPS_PR_PREPARED;isReadOnly=true]true")
        print(f"##vso[task.setvariable variable=AGENTOPS_PR_SHA;isReadOnly=true]{source_sha}")
        print(f"##vso[task.setvariable variable=AGENTOPS_PR_NUMBER;isReadOnly=true]{match.group(1)}")
    elif env.get("GITHUB_ENV"):
        with Path(env["GITHUB_ENV"]).open("a", encoding="utf-8") as handle:
            handle.write(f"AGENTOPS_PR_SHA={sha}\n")
            handle.write(f"AGENTOPS_PR_NUMBER={env.get('AGENTOPS_PR_NUMBER', '')}\n")


def publish_ado_status(root: Path) -> None:
    """Publish a revision-bound required status from the protected evaluator."""
    env = os.environ
    record_path = root / _INPUT_DIR / "revision.json"
    if not record_path.exists():
        raise ValueError("No verified PR revision is available for status publication")
    record = json.loads(record_path.read_text(encoding="utf-8"))
    # These values are explicitly mapped from the protected PR variable group.
    collection = env["AGENTOPS_ADO_COLLECTION_URI"].rstrip("/")
    _validate_https(collection)
    project = urllib.parse.quote(env["AGENTOPS_ADO_PROJECT_ID"], safe="")
    repository = urllib.parse.quote(env["AGENTOPS_ADO_REPOSITORY_ID"], safe="")
    base = f"{collection}/{project}/_apis/git/repositories/{repository}/pullRequests/{record['pr']}"
    headers = {"Authorization": f"Bearer {_ado_status_token()}"}

    def request(url: str, payload: dict[str, Any] | None = None) -> Any:
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(
            url, data=data, headers={**headers, "Content-Type": "application/json"},
        )
        # No redirects to another credential destination.
        from agentops.pipeline.invocations import _SameOriginRedirectHandler

        with urllib.request.build_opener(_SameOriginRedirectHandler()).open(req, timeout=30) as response:
            return json.loads(response.read())

    iterations = request(f"{base}/iterations?api-version=7.1")["value"]
    latest = max(iterations, key=lambda item: item["id"])
    if latest["sourceRefCommit"]["commitId"] != record["source_sha"]:
        raise ValueError("PR changed since evaluation, refusing to approve a newer revision")
    succeeded = env.get("AGENT_JOBSTATUS", "").lower() == "succeeded"
    request(
        f"{base}/statuses?api-version=7.1",
        {
            "state": "succeeded" if succeeded else "failed",
            "description": "AgentOps trusted PR evaluation",
            "context": {"name": "pr-eval", "genre": "agentops"},
            "iterationId": latest["id"],
            "targetUrl": f"{collection}/{project}/_build/results?buildId={env['BUILD_BUILDID']}",
        },
    )


def _ado_status_token() -> str:
    # Do not publish trusted evidence under the PR job's Build Service identity.
    from azure.identity import AzureCliCredential

    return AzureCliCredential(process_timeout=30).get_token(
        "499b84ac-1321-427f-aa17-267ca6975798/.default"
    ).token


def main() -> None:
    try:
        if sys.argv[1:] == ["status"]:
            publish_ado_status(Path.cwd())
        elif not sys.argv[1:]:
            prepare_from_environment(Path.cwd())
        else:
            raise ValueError("Unsupported internal PR gate operation")
    except (ValueError, KeyError, OSError, YAMLError, subprocess.SubprocessError) as exc:
        message = str(exc).replace("##vso[", "# #vso[").replace("\n", "\\n").replace("\r", "\\r")
        print(f"AgentOps PR gate error: {message}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
