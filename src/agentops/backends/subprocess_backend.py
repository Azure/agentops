"""Subprocess backend implementation for AgentOps."""
from __future__ import annotations

import os
import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from agentops.backends.base import BackendExecutionResult, BackendRunContext


def _to_utc_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


class SubprocessBackend:
    def build_command(self, context: BackendRunContext) -> list[str]:
        backend = context.backend_config
        if backend.command is None:
            raise ValueError("backend.command is required")

        replacements = {
            "{bundle_path}": str(context.bundle_path),
            "{dataset_path}": str(context.dataset_path),
            "{backend_output_dir}": str(context.backend_output_dir),
        }

        command = [backend.command]
        for arg in backend.args:
            rendered_arg = arg
            for key, value in replacements.items():
                rendered_arg = rendered_arg.replace(key, value)
            command.append(rendered_arg)
        return command

    def execute(self, context: BackendRunContext) -> BackendExecutionResult:
        context.backend_output_dir.mkdir(parents=True, exist_ok=True)

        stdout_path = context.backend_output_dir / "backend.stdout.log"
        stderr_path = context.backend_output_dir / "backend.stderr.log"

        command = self.build_command(context)
        command_display = shlex.join(command)

        env = os.environ.copy()
        env.update(context.backend_config.env)

        started = datetime.now(timezone.utc)
        timeout_seconds = context.backend_config.timeout_seconds

        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                env=env,
                timeout=timeout_seconds,
            )
            stdout_text = _safe_text(completed.stdout)
            stderr_text = _safe_text(completed.stderr)
            exit_code = completed.returncode
        except subprocess.TimeoutExpired as exc:
            stdout_text = _safe_text(exc.stdout)
            stderr_text = _safe_text(exc.stderr)
            exit_code = 124

        finished = datetime.now(timezone.utc)

        stdout_path.write_text(stdout_text, encoding="utf-8")
        stderr_path.write_text(stderr_text, encoding="utf-8")

        return BackendExecutionResult(
            backend=context.backend_config.type,
            command=command_display,
            started_at=_to_utc_timestamp(started),
            finished_at=_to_utc_timestamp(finished),
            duration_seconds=(finished - started).total_seconds(),
            exit_code=exit_code,
            stdout_file=stdout_path,
            stderr_file=stderr_path,
        )
