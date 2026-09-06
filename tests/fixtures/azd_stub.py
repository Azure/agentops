"""Reusable Azure Developer CLI subprocess double for tests.

Lets a test script an ordered sequence of azd invocations, assert the exact
argv of each one, and assert that no unexpected command was run. Install it
with :meth:`AzdStub.install`, which patches ``subprocess.run`` for the duration
of the test via ``monkeypatch``.

Both azd adapters call ``subprocess.run`` through
``agentops.pipeline.azd_runner._run_command`` when no progress callback is
supplied, so patching ``subprocess.run`` covers the whole azd boundary without
reaching the network or requiring azd to be installed.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Sequence


@dataclass
class _Expectation:
    """One scripted azd invocation."""

    match: tuple[str, ...]
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""
    side_effect: Optional[Callable[[list[str]], None]] = None


class UnexpectedAzdCommand(AssertionError):
    """Raised when the stub receives a command it was not scripted for."""


class _FakePopen:
    """Minimal ``subprocess.Popen`` stand-in for the progress-heartbeat path.

    ``_run_command_with_progress`` only needs ``args``, ``poll``, ``wait``, and
    ``kill``. The process is already "finished" when constructed, so the
    heartbeat loop exits on its first iteration.
    """

    def __init__(self, args: list[str], returncode: int) -> None:
        self.args = args
        self.returncode = returncode

    def poll(self) -> int:
        return self.returncode

    def wait(self, timeout: Optional[float] = None) -> int:
        return self.returncode

    def kill(self) -> None:  # pragma: no cover - never reached; poll ends the loop
        return None


class AzdStub:
    """An ordered, assertion-friendly stand-in for the azd CLI."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self._queue: list[_Expectation] = []

    # ------------------------------------------------------------------
    # Scripting
    # ------------------------------------------------------------------
    def expect(
        self,
        *match: str,
        returncode: int = 0,
        stdout: str = "",
        stderr: str = "",
        side_effect: Optional[Callable[[list[str]], None]] = None,
    ) -> "AzdStub":
        """Queue the next expected invocation.

        ``match`` tokens must all appear in the joined argv, in any position.
        Use enough tokens to identify the command unambiguously.
        """

        self._queue.append(
            _Expectation(
                match=tuple(match),
                returncode=returncode,
                stdout=stdout,
                stderr=stderr,
                side_effect=side_effect,
            )
        )
        return self

    def expect_json(self, *match: str, payload: Any, returncode: int = 0) -> "AzdStub":
        """Queue an invocation whose stdout is ``payload`` serialized as JSON."""

        return self.expect(
            *match,
            returncode=returncode,
            stdout=json.dumps(payload),
        )

    def expect_output_file(
        self,
        *match: str,
        payload: Any,
        flag: str = "--output-file",
        returncode: int = 0,
        stdout: str = "",
    ) -> "AzdStub":
        """Queue an invocation that writes ``payload`` to the path after ``flag``."""

        return self.expect(
            *match,
            returncode=returncode,
            stdout=stdout,
            side_effect=write_json_to_flag(flag, payload),
        )

    # ------------------------------------------------------------------
    # Installation
    # ------------------------------------------------------------------
    def install(self, monkeypatch: Any) -> "AzdStub":
        """Patch both ``subprocess.run`` and ``subprocess.Popen``.

        ``azd_runner._run_command`` uses ``subprocess.run`` when no progress
        callback is supplied, and ``subprocess.Popen`` when one is (the
        heartbeat path). The legacy adapter is driven with a progress callback
        by the orchestrator, so patching only ``run`` would let the real azd
        binary be invoked from an integration test.
        """

        monkeypatch.setattr(subprocess, "run", self)
        monkeypatch.setattr(subprocess, "Popen", self._popen)
        return self

    def _popen(self, command: Sequence[str], **kwargs: Any) -> "_FakePopen":
        completed = self(command, **kwargs)
        for stream_name in ("stdout", "stderr"):
            target = kwargs.get(stream_name)
            payload = getattr(completed, stream_name) or ""
            if payload and hasattr(target, "write"):
                target.write(payload)
        return _FakePopen(list(completed.args), completed.returncode)

    # ------------------------------------------------------------------
    # Invocation
    # ------------------------------------------------------------------
    def __call__(self, command: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess:
        argv = [str(part) for part in command]
        self.calls.append(argv)
        joined = " ".join(argv)

        if not self._queue:
            raise UnexpectedAzdCommand(
                f"azd was invoked more times than scripted.\n"
                f"Unexpected: {joined}\n"
                f"Calls so far:\n" + self.describe_calls()
            )

        expectation = self._queue.pop(0)
        missing = [token for token in expectation.match if token not in argv]
        if missing:
            raise UnexpectedAzdCommand(
                f"azd invocation did not match the next scripted command.\n"
                f"Expected tokens: {expectation.match}\n"
                f"Missing: {missing}\n"
                f"Actual: {joined}"
            )

        if expectation.side_effect is not None:
            expectation.side_effect(argv)

        return subprocess.CompletedProcess(
            argv,
            expectation.returncode,
            stdout=expectation.stdout,
            stderr=expectation.stderr,
        )

    # ------------------------------------------------------------------
    # Assertions and inspection
    # ------------------------------------------------------------------
    @property
    def commands(self) -> list[str]:
        """Every invocation so far, as joined command strings."""

        return [" ".join(argv) for argv in self.calls]

    def describe_calls(self) -> str:
        if not self.calls:
            return "  (none)"
        return "\n".join(f"  {index}: {line}" for index, line in enumerate(self.commands))

    def assert_exhausted(self) -> None:
        """Assert every scripted invocation actually happened."""

        if self._queue:
            remaining = [expectation.match for expectation in self._queue]
            raise AssertionError(
                f"azd was invoked fewer times than scripted. Remaining: {remaining}\n"
                f"Calls:\n" + self.describe_calls()
            )

    def assert_never_invoked(self) -> None:
        """Assert azd was never called at all."""

        if self.calls:
            raise AssertionError(
                "expected azd to never be invoked, but it was:\n" + self.describe_calls()
            )

    def find(self, *tokens: str) -> list[list[str]]:
        """Return every recorded invocation containing all ``tokens`` as argv entries.

        Matching is on exact argv tokens, never on a substring of the joined
        command. Paths passed to azd routinely contain words like ``run`` or
        ``list`` (pytest tmp dirs are named after the test), and substring
        matching would silently select the wrong invocation.
        """

        return [argv for argv in self.calls if all(token in argv for token in tokens)]

    def assert_no_call_containing(self, *tokens: str) -> None:
        matches = self.find(*tokens)
        if matches:
            raise AssertionError(
                f"expected no azd invocation containing {tokens}, found:\n"
                + "\n".join(f"  {' '.join(argv)}" for argv in matches)
            )


def arg_after(argv: Sequence[str], flag: str) -> str:
    """Return the value following ``flag`` in ``argv``."""

    parts = [str(part) for part in argv]
    if flag not in parts:
        raise AssertionError(f"{flag} not present in: {' '.join(parts)}")
    index = parts.index(flag)
    if index + 1 >= len(parts):
        raise AssertionError(f"{flag} has no value in: {' '.join(parts)}")
    return parts[index + 1]


def write_json_to_flag(flag: str, payload: Any) -> Callable[[list[str]], None]:
    """Build a side effect that writes ``payload`` to the path following ``flag``."""

    def _write(argv: list[str]) -> None:
        target = Path(arg_after(argv, flag))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload), encoding="utf-8")

    return _write


def extension_list_json(*extension_ids: str) -> str:
    """Build a structured ``azd extension list --installed -o json`` payload."""

    return json.dumps(
        [
            {
                "id": extension_id,
                "name": f"{extension_id} (Beta)",
                "version": "1.0.0-beta.1",
                "installedVersion": "1.0.0-beta.1",
                "updateAvailable": False,
                "source": "azd",
            }
            for extension_id in extension_ids
        ]
    )


def extension_list_table(*rows: tuple[str, str]) -> str:
    """Build a human-readable ``azd extension list`` table.

    Each row is ``(extension_id, status)``. Statuses observed in the wild
    include ``Up to date``, ``Update available``, ``Not installed``, and
    ``Incompatible``. A ``Not installed`` row still contains the extension id,
    which is exactly why substring scanning is unsafe.
    """

    header = "ID                         NAME                STATUS          INSTALLED"
    lines = [header, "-" * len(header)]
    for extension_id, status in rows:
        installed = "-" if status == "Not installed" else "1.0.0-bet…"
        lines.append(f"{extension_id:<26} {extension_id:<19} {status:<15} {installed}")
    return "\n".join(lines) + "\n"
