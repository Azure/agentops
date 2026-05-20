"""Pre-flight checks shared by `agentops doctor`, `agentops cockpit`,
and `agentops agent serve`.

Goal: surface every reason the agent might silently misbehave *before*
the user waits ~20 seconds for the first cockpit render or the first
doctor run, and tell them exactly what to fix.

The checks are intentionally fast and best-effort:

* No retries — a single shot with the standard 30-second
  ``DefaultAzureCredential(process_timeout=30)`` timeout.
* Failures never raise into the CLI; they return a status row.
* Optional checks (Foundry / App Insights / ARM) are skipped cleanly
  when the relevant env var or config is absent.

Callers receive a list of :class:`PreflightCheck` rows and decide what
to do with them. The default policy is *advisory* — print warnings and
continue. Strict CI pipelines can pass ``--strict-preflight`` to make
the CLI exit non-zero on any failure.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Literal, Optional

log = logging.getLogger(__name__)


_Status = Literal["ok", "warn", "skip", "fail"]


@dataclass(frozen=True)
class PreflightCheck:
    """One row in the pre-flight summary.

    ``name`` is the stable internal id; ``display_name`` is what
    appears in the terminal (e.g. ``Azure authentication``).
    ``remediation`` is the one-line "do this to fix it" hint shown
    indented under the message for ``warn`` / ``fail`` rows.
    """

    name: str
    status: _Status
    message: str
    display_name: str = ""
    remediation: str = ""
    duration_seconds: float = 0.0


@dataclass(frozen=True)
class PreflightReport:
    """Aggregate result of a pre-flight run."""

    checks: List[PreflightCheck]

    @property
    def has_failures(self) -> bool:
        return any(c.status == "fail" for c in self.checks)

    @property
    def has_warnings(self) -> bool:
        return any(c.status == "warn" for c in self.checks)


_AZURE_RM_SCOPE = "https://management.azure.com/.default"


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def _check_workspace(workspace: Path) -> PreflightCheck:
    """The workspace must exist and contain `.agentops/`."""
    started = time.time()
    if not workspace.exists() or not workspace.is_dir():
        return PreflightCheck(
            name="workspace",
            display_name="Workspace",
            status="fail",
            message=f"Workspace path does not exist: {workspace}",
            remediation="Create the directory or pass --workspace <path>.",
            duration_seconds=time.time() - started,
        )
    agentops_dir = workspace / ".agentops"
    if not agentops_dir.is_dir():
        return PreflightCheck(
            name="workspace",
            display_name="Workspace",
            status="warn",
            message=(
                f"No `.agentops/` directory in {workspace}. Checks that "
                "depend on workspace files will be silent."
            ),
            remediation="Run `agentops init` to scaffold the workspace.",
            duration_seconds=time.time() - started,
        )
    return PreflightCheck(
        name="workspace",
        display_name="Workspace",
        status="ok",
        message=str(workspace),
        duration_seconds=time.time() - started,
    )


def _check_azure_cli() -> PreflightCheck:
    """`DefaultAzureCredential` can acquire an ARM token.

    On a dev box this is shorthand for "`az login` is current"; on a
    CI runner it covers any of the workload-identity / managed-identity
    sub-credentials.
    """
    started = time.time()
    try:
        from azure.identity import DefaultAzureCredential
    except ImportError:
        return PreflightCheck(
            name="azure_auth",
            display_name="Azure authentication",
            status="skip",
            message="azure-identity SDK not installed.",
            remediation="Install the agent extra: pip install agentops-toolkit[agent].",
            duration_seconds=time.time() - started,
        )
    try:
        cred = DefaultAzureCredential(
            exclude_developer_cli_credential=True, process_timeout=30
        )
        token = cred.get_token(_AZURE_RM_SCOPE)
    except Exception as exc:  # noqa: BLE001
        # Humanize the wall-of-text DefaultAzureCredential failure.
        text = str(exc).lower()
        if (
            "azureclicredential: failed to invoke the azure cli" in text
            or "no accounts were found in the cache" in text
        ):
            msg = "Not signed in to Azure."
            remediation = (
                "Run `az login` in this terminal, then re-run the command."
            )
        else:
            snippet = str(exc).splitlines()[0][:200]
            msg = f"Azure token acquisition failed: {snippet}"
            remediation = (
                "Check your network, your `az login` state, and the "
                "cockpit server logs at DEBUG for the full credential "
                "chain."
            )
        return PreflightCheck(
            name="azure_auth",
            display_name="Azure authentication",
            status="fail",
            message=msg,
            remediation=remediation,
            duration_seconds=time.time() - started,
        )
    expires_in = int(token.expires_on - time.time())
    return PreflightCheck(
        name="azure_auth",
        display_name="Azure authentication",
        status="ok",
        message=f"ARM token acquired (expires in {expires_in // 60} min)",
        duration_seconds=time.time() - started,
    )


def _check_foundry_project() -> PreflightCheck:
    """`AZURE_AI_FOUNDRY_PROJECT_ENDPOINT` reachable + App Insights wired."""
    started = time.time()
    endpoint = os.getenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT")
    if not endpoint:
        return PreflightCheck(
            name="foundry_project",
            display_name="Foundry project",
            status="skip",
            message="AZURE_AI_FOUNDRY_PROJECT_ENDPOINT is not set.",
            remediation=(
                "Export AZURE_AI_FOUNDRY_PROJECT_ENDPOINT=<your-project-url> "
                "to enable Foundry-aware checks."
            ),
            duration_seconds=time.time() - started,
        )
    try:
        from agentops.utils.foundry_discovery import (
            resolve_appinsights_connection_with_reason,
        )
    except ImportError:
        return PreflightCheck(
            name="foundry_project",
            display_name="Foundry project",
            status="skip",
            message="agentops.utils.foundry_discovery not available.",
            duration_seconds=time.time() - started,
        )
    conn, reason = resolve_appinsights_connection_with_reason(endpoint)
    if conn:
        return PreflightCheck(
            name="foundry_project",
            display_name="Foundry project",
            status="ok",
            message="Project reachable; App Insights auto-discovered.",
            duration_seconds=time.time() - started,
        )
    return PreflightCheck(
        name="foundry_project",
        display_name="Foundry project",
        status="warn",
        message=f"Discovery failed — {reason}",
        remediation=(
            "Confirm the signed-in identity has Reader on the project "
            "resource group, then re-run."
        ),
        duration_seconds=time.time() - started,
    )


def _check_application_insights_env() -> PreflightCheck:
    """Heads-up when neither env var nor Foundry discovery yields a
    connection string. The production telemetry tile will stay grey."""
    started = time.time()
    if os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING") or os.getenv(
        "AGENTOPS_APPLICATIONINSIGHTS_CONNECTION_STRING"
    ):
        return PreflightCheck(
            name="app_insights",
            display_name="Application Insights",
            status="ok",
            message="APPLICATIONINSIGHTS_CONNECTION_STRING is set.",
            duration_seconds=time.time() - started,
        )
    # Try Foundry discovery as a fallback (uses the same cached helper).
    endpoint = os.getenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT")
    if endpoint:
        try:
            from agentops.utils.foundry_discovery import (
                resolve_appinsights_connection_with_reason,
            )
            conn, _ = resolve_appinsights_connection_with_reason(endpoint)
            if conn:
                return PreflightCheck(
                    name="app_insights",
                    display_name="Application Insights",
                    status="ok",
                    message="Resolved via Foundry discovery.",
                    duration_seconds=time.time() - started,
                )
        except ImportError:
            pass
    return PreflightCheck(
        name="app_insights",
        display_name="Application Insights",
        status="warn",
        message=(
            "No connection string available; production telemetry will be empty."
        ),
        remediation=(
            "Wire App Insights to your Foundry project (Project details "
            "→ Connected resources → Add connection → Application "
            "Insights) or set APPLICATIONINSIGHTS_CONNECTION_STRING."
        ),
        duration_seconds=time.time() - started,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


_Scope = Literal["doctor", "cockpit", "agent_serve"]


def run_preflight(
    workspace: Path,
    *,
    scope: _Scope = "doctor",
) -> PreflightReport:
    """Run every check applicable to ``scope`` and return the report."""
    checks: List[PreflightCheck] = []

    checks.append(_check_workspace(workspace))

    # Auth is needed by every scope that talks to Azure.
    checks.append(_check_azure_cli())

    # Foundry / App Insights probes are advisory; they help the user
    # understand *why* certain sources will be silent rather than
    # blocking the run.
    checks.append(_check_foundry_project())
    checks.append(_check_application_insights_env())

    return PreflightReport(checks=checks)


def format_report(
    report: PreflightReport,
    *,
    color: Optional[bool] = None,
) -> str:
    """Render the report as a human-friendly multi-line summary.

    When ``color`` is ``None`` (the default), ANSI escapes are emitted
    only when ``stderr`` is a TTY and the ``NO_COLOR`` env var is not
    set — so piping the output into a log file or CI buffer produces
    clean ASCII automatically.

    Layout
    ------
    ::

        AgentOps pre-flight   3 ok · 1 warning · 0 failed

          ✓ Workspace             /repo/path
          ✓ Azure authentication  ARM token acquired (expires in 89 min)
          ✓ Foundry project       Project reachable; App Insights auto-discovered.
          ⚠ Application Insights  No connection string available; production telemetry will be empty.
                                  → Wire App Insights in Foundry or set APPLICATIONINSIGHTS_CONNECTION_STRING.

    When every check is ``ok`` the body is collapsed to a single
    one-liner so a healthy run does not clutter the terminal.
    """
    if color is None:
        import sys
        color = (
            sys.stderr.isatty()
            and not os.environ.get("NO_COLOR")
        )
    counts = {"ok": 0, "warn": 0, "skip": 0, "fail": 0}
    for c in report.checks:
        counts[c.status] = counts.get(c.status, 0) + 1

    # ANSI palette — green / yellow / dim / red.
    palette = {
        "ok": "32",
        "warn": "33",
        "skip": "37",
        "fail": "31",
    }
    glyphs = {
        "ok": "\u2713",   # check
        "warn": "\u26a0", # warning sign
        "skip": "\u00b7", # middle dot
        "fail": "\u2717", # ballot x
    }

    def _color(text: str, status: _Status) -> str:
        if not color:
            return text
        return f"\x1b[{palette[status]}m{text}\x1b[0m"

    # Headline summary — counts are shown in their tone color so the
    # eye lands on whatever is non-zero.
    pieces = [_color(f"{counts['ok']} ok", "ok")]
    if counts["warn"]:
        pieces.append(_color(f"{counts['warn']} warning", "warn"))
    if counts["fail"]:
        pieces.append(_color(f"{counts['fail']} failed", "fail"))
    if counts["skip"]:
        pieces.append(_color(f"{counts['skip']} skipped", "skip"))
    summary = " \u00b7 ".join(pieces)
    lines = [f"AgentOps pre-flight   {summary}"]

    # Healthy-run short circuit: no per-check rows when everything passed.
    if not report.has_failures and not report.has_warnings and counts["skip"] == 0:
        return lines[0]

    lines.append("")

    # Compute display-name column width once for clean alignment.
    label_w = max(
        (len(c.display_name or c.name) for c in report.checks),
        default=12,
    )

    for c in report.checks:
        glyph = _color(glyphs[c.status], c.status)
        label = (c.display_name or c.name).ljust(label_w)
        lines.append(f"  {glyph} {label}  {c.message}")
        # Show the remediation hint for warn / fail (action required)
        # AND for skip (so the user knows how to enable the check).
        if c.remediation and c.status in ("warn", "fail", "skip"):
            indent = " " * (label_w + 5)
            lines.append(f"{indent}\u2192 {c.remediation}")

    return "\n".join(lines)
