"""Backend protocol and shared execution models."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from agentops.core.models import BackendConfig


@dataclass(frozen=True)
class BackendRunContext:
    backend_config: BackendConfig
    bundle_path: Path
    dataset_path: Path
    backend_output_dir: Path


@dataclass(frozen=True)
class BackendExecutionResult:
    backend: str
    command: str
    started_at: str
    finished_at: str
    duration_seconds: float
    exit_code: int
    stdout_file: Path
    stderr_file: Path


class Backend(Protocol):
    def execute(self, context: BackendRunContext) -> BackendExecutionResult:
        """Execute backend work and return normalized execution metadata."""
