"""Shared Azure credential factory and concise source-error formatting."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
from typing import Any, Optional

_LOCK = threading.Lock()
_CREDENTIAL_CACHE: dict[tuple[bool, int], Any] = {}
_AZ_CLI_AVAILABLE: Optional[bool] = None


def _az_cli_logged_in(process_timeout: int) -> bool:
    """Return whether an Azure CLI sign-in is available, caching the probe."""

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
    """Return one cached Azure credential, preferring an active CLI sign-in."""

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
    """Forget cached credentials and CLI-probe state."""

    global _AZ_CLI_AVAILABLE
    with _LOCK:
        _CREDENTIAL_CACHE.clear()
        _AZ_CLI_AVAILABLE = None


def summarise_credential_error(exc: BaseException) -> str:
    """Reduce an Azure identity credential-chain error to one line."""

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
        leg_name, separator, _ = stripped.partition(":")
        if (
            separator
            and leg_name
            and " " not in leg_name
            and leg_name.endswith("Credential")
        ):
            failed_legs.append(leg_name)

    if failed_legs:
        preview = ", ".join(failed_legs[:4])
        if len(failed_legs) > 4:
            preview += f", +{len(failed_legs) - 4} more"
        summary = f"{summary} (chain: {preview})"
    return summary


def is_credential_error(exc: BaseException) -> bool:
    """Best-effort detector for Azure identity authentication errors."""

    if type(exc).__name__ in {
        "ClientAuthenticationError",
        "CredentialUnavailableError",
    }:
        return True
    try:
        from azure.core.exceptions import ClientAuthenticationError

        return isinstance(exc, ClientAuthenticationError)
    except ImportError:
        return False


def format_source_error(exc: BaseException) -> str:
    """Format a source error without emitting a credential-chain dump."""

    if is_credential_error(exc):
        return summarise_credential_error(exc)
    return str(exc)


def log_source_error(
    logger: logging.Logger, message_prefix: str, exc: BaseException
) -> str:
    """Log credential errors at info and other source errors at warning."""

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
