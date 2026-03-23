from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from agentops.backends.base import BackendRunContext
from agentops.backends.subprocess_backend import SubprocessBackend
from agentops.core.models import BackendConfig


def _build_context(tmp_path: Path) -> BackendRunContext:
    backend_config = BackendConfig(
        type="subprocess",
        command="python",
        args=[
            "-m",
            "fake_eval_runner",
            "--bundle",
            "{bundle_path}",
            "--dataset",
            "{dataset_path}",
            "--output",
            "{backend_output_dir}",
        ],
        env={"CUSTOM_VAR": "value"},
        timeout_seconds=33,
    )
    return BackendRunContext(
        backend_config=backend_config,
        bundle_path=tmp_path / "bundle.yaml",
        dataset_path=tmp_path / "dataset.yaml",
        backend_output_dir=tmp_path / "backend-out",
    )


def test_build_command_substitutes_placeholders(tmp_path: Path) -> None:
    context = _build_context(tmp_path)
    backend = SubprocessBackend()

    command = backend.build_command(context)

    assert command[0] == "python"
    assert str(context.bundle_path) in command
    assert str(context.dataset_path) in command
    assert str(context.backend_output_dir) in command
    assert "{bundle_path}" not in command
    assert "{dataset_path}" not in command
    assert "{backend_output_dir}" not in command


def test_execute_builds_command_and_writes_logs(tmp_path: Path) -> None:
    context = _build_context(tmp_path)
    backend = SubprocessBackend()

    fake_completed = CompletedProcess(
        args=["python", "-m", "fake_eval_runner"],
        returncode=0,
        stdout="ok stdout",
        stderr="ok stderr",
    )

    with patch(
        "agentops.backends.subprocess_backend.subprocess.run",
        return_value=fake_completed,
    ) as run_mock:
        result = backend.execute(context)

    run_kwargs = run_mock.call_args.kwargs
    called_command = run_mock.call_args.args[0]

    assert called_command[0] == "python"
    assert str(context.bundle_path) in called_command
    assert str(context.dataset_path) in called_command
    assert str(context.backend_output_dir) in called_command
    assert run_kwargs["timeout"] == 33
    assert run_kwargs["capture_output"] is True
    assert run_kwargs["text"] is True
    assert run_kwargs["check"] is False
    assert run_kwargs["env"]["CUSTOM_VAR"] == "value"

    assert result.exit_code == 0
    assert result.backend == "subprocess"
    assert result.stdout_file.exists()
    assert result.stderr_file.exists()
    assert result.stdout_file.read_text(encoding="utf-8") == "ok stdout"
    assert result.stderr_file.read_text(encoding="utf-8") == "ok stderr"
