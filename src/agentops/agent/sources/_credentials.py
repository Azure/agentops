"""Shared Azure credential factory + concise error formatting for Doctor sources.

Why a shared credential?
------------------------

Each Doctor source previously instantiated its own
:class:`azure.identity.DefaultAzureCredential` and called ``get_token`` on it.
``DefaultAzureCredential`` walks every credential in its chain on each
``get_token`` call, and on Windows the ``AzureCliCredential`` /
``AzurePowerShellCredential`` legs spawn ``az.cmd`` / ``powershell.exe``
subprocesses whose cold-start is flaky (anti-virus, paging, .NET warmup).
When the subprocess fails for any reason, azure-identity raises a
``ClientAuthenticationError`` whose ``str()`` dumps the **entire** chain to
the log:

    DefaultAzureCredential failed to retrieve a token ...
    Attempted credentials:
            EnvironmentCredential: ...
            WorkloadIdentityCredential: ...
            ManagedIdentityCredential: ...
            ...

A single shared credential per process caches access tokens by scope, so the
expensive chain walk runs at most once per scope and subsequent reads use the
cached token until it expires. This dramatically reduces the surface for
transient Windows-only flakes between sources.

When the developer has the Azure CLI installed and an active ``az login``,
we prefer :class:`AzureCliCredential` directly. This skips the noisy chain
walk entirely, inherits the CLI's on-disk token cache, and returns a single
crisp error message when something is wrong (instead of dumping eight
``Attempted credentials:`` entries).

Why summarise errors?
---------------------

When an auth call genuinely fails, dumping the multi-line chain into the
Doctor terminal is noisy and unhelpful — every consumer of these sources
already returns a structured ``diagnostics`` dict the report uses. The
public :func:`summarise_credential_error` helper produces a single-line
human-friendly reason string for the log line.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
from typing import Any, Optional

log = logging.getLogger(__name__)

_LOCK = threading.Lock()
_CREDENTIAL_CACHE: dict[tuple[bool, int], Any] = {}
_AZ_CLI_AVAILABLE: Optional[bool] = None


def _az_cli_logged_in(process_timeout: int) -> bool:
    """Return True when ``az account show`` succeeds within the timeout.

    Caches the result for the lifetime of the process so we only pay the
    detection cost once. Auto-disables under pytest unless the test opts
    in via the ``AGENTOPS_ALLOW_AZ_CLI_PROBE`` environment variable, so
    test runs never spawn a real ``az`` subprocess by accident.
    """
    global _AZ_CLI_AVAILABLE
    if _AZ_CLI_AVAILABLE is not None:
        return _AZ_CLI_AVAILABLE

    if os.environ.get("PYTEST_CURRENT_TEST") and not os.environ.get(
        "AGENTOPS_ALLOW_AZ_CLI_PROBE"
    ):
        _AZ_CLI_AVAILABLE = False
        return False

    az_path = shutil.which("az") or shutil.which("az.cmd")
    if not az_path:
        _AZ_CLI_AVAILABLE = False
        return False

    try:
        completed = subprocess.run(
            [az_path, "account", "show", "--query", "id", "-o", "tsv"],
            capture_output=True,
            text=True,
            timeout=max(process_timeout, 60),
            check=False,
        )
        _AZ_CLI_AVAILABLE = (
            completed.returncode == 0 and bool(completed.stdout.strip())
        )
    except (subprocess.TimeoutExpired, OSError):
        _AZ_CLI_AVAILABLE = False
    return _AZ_CLI_AVAILABLE


def get_shared_credential(
    *,
    exclude_developer_cli_credential: bool = False,
    process_timeout: int = 30,
) -> Any:
    """Return a process-wide credential for Doctor sources.

    Prefers :class:`AzureCliCredential` when ``az login`` is active — that
    skips the multi-leg DefaultAzureCredential chain, inherits the CLI's
    token cache, and produces crisp single-line errors. Falls back to
    :class:`DefaultAzureCredential` (with a longer Windows-friendly
    ``process_timeout``) otherwise.

    The credential is cached per ``(exclude_developer_cli_credential,
    process_timeout)`` combination so callers that need slightly different
    chains do not collide. azure-identity itself caches access tokens per
    scope on each credential instance, so reusing the same instance across
    sources avoids re-walking the credential chain on every ``get_token``
    call.

    Raises:
        ImportError: When the ``azure-identity`` package is not installed.
    """

    from azure.identity import DefaultAzureCredential

    key = (bool(exclude_developer_cli_credential), int(process_timeout))
    with _LOCK:
        cached = _CREDENTIAL_CACHE.get(key)
        if cached is not None:
            return cached

        credential: Any = None
        if _az_cli_logged_in(process_timeout):
            try:
                from azure.identity import AzureCliCredential

                credential = AzureCliCredential(process_timeout=process_timeout)
            except ImportError:
                credential = None
        if credential is None:
            credential = DefaultAzureCredential(
                exclude_developer_cli_credential=exclude_developer_cli_credential,
                process_timeout=process_timeout,
            )
        _CREDENTIAL_CACHE[key] = credential
        return credential


def reset_shared_credentials() -> None:
    """Forget all cached credentials (intended for tests)."""

    global _AZ_CLI_AVAILABLE
    with _LOCK:
        _CREDENTIAL_CACHE.clear()
        _AZ_CLI_AVAILABLE = None


def summarise_credential_error(exc: BaseException) -> str:
    """Return a single-line summary of an azure-identity error.

    ``ClientAuthenticationError.__str__`` dumps the entire credential chain
    (every leg, with troubleshooting URLs). This helper extracts just the
    headline and, when present, names the legs that failed so logs stay
    readable.
    """

    raw = str(exc).strip()
    if not raw:
        return exc.__class__.__name__

    first_line, _, rest = raw.partition("\n")
    summary = first_line.strip()

    failed_legs: list[str] = []
    for line in rest.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("Attempted", "To mitigate", "Visit ")):
            continue
        leg_name, sep, _ = stripped.partition(":")
        if sep and leg_name and " " not in leg_name and leg_name.endswith("Credential"):
            failed_legs.append(leg_name)

    if failed_legs:
        # Trim to the first few legs to avoid recreating the dump.
        preview = ", ".join(failed_legs[:4])
        if len(failed_legs) > 4:
            preview += f", +{len(failed_legs) - 4} more"
        summary = f"{summary} (chain: {preview})"
    return summary


def is_credential_error(exc: BaseException) -> bool:
    """Best-effort detector for azure-identity authentication errors."""

    name = type(exc).__name__
    if name in {"ClientAuthenticationError", "CredentialUnavailableError"}:
        return True
    try:
        from azure.core.exceptions import ClientAuthenticationError  # type: ignore[import-not-found]

        return isinstance(exc, ClientAuthenticationError)
    except ImportError:
        return False


def format_source_error(exc: BaseException) -> str:
    """Format any source-side exception for log output.

    Uses :func:`summarise_credential_error` for azure-identity errors and
    falls back to the regular ``str(exc)`` otherwise.
    """

    if is_credential_error(exc):
        return summarise_credential_error(exc)
    return str(exc)


def log_source_error(
    logger: logging.Logger, message_prefix: str, exc: BaseException
) -> str:
    """Log a source error at the right severity and return the reason text.

    Credential acquisition flakes are noisy on Windows (az.cmd cold-starts,
    PowerShell missing, broker package not installed) but they almost never
    indicate a real problem — Doctor sources are opt-in and simply skip when
    they cannot authenticate. We log those at INFO so the terminal stays
    clean. Genuine errors (network failures, malformed responses, etc.) are
    still logged at WARNING.
    """
    reason = format_source_error(exc)
    if is_credential_error(exc):
        logger.info("%s: %s", message_prefix, reason)
    else:
        logger.warning("%s: %s", message_prefix, reason)
    return reason


__all__ = [
    "format_source_error",
    "get_shared_credential",
    "is_credential_error",
    "log_source_error",
    "reset_shared_credentials",
    "summarise_credential_error",
]
