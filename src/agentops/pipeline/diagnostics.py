"""Shared runtime diagnostics for pipeline errors."""

from __future__ import annotations

import re


_TENANT_MISMATCH_MARKERS = (
    "Tenant provided in token does not match resource token",
    "Tenant provided in token does not match resource tenant",
    "does not match resource tenant",
)

_TENANT_MISMATCH_GUIDANCE = (
    " Check that `az login` is using the same tenant as the Foundry project, "
    "or run `az login --tenant <tenant-id>`."
)


def with_tenant_mismatch_guidance(message: str) -> str:
    """Append actionable Azure tenant guidance to matching error messages."""
    if "az login --tenant" in message:
        return message
    if any(marker in message for marker in _TENANT_MISMATCH_MARKERS):
        return f"{message}{_TENANT_MISMATCH_GUIDANCE}"
    return message


def summarize_exception(exc: BaseException) -> str:
    """Return a concise, user-facing summary for known noisy SDK errors."""
    message = with_tenant_mismatch_guidance(str(exc))
    validation_messages = _extract_foundry_validation_messages(message)
    if validation_messages:
        joined = "; ".join(validation_messages)
        return f"Foundry cloud validation failed: {joined}"
    return message


def _extract_foundry_validation_messages(message: str) -> list[str]:
    if "Evaluation failed validation" not in message:
        return []
    matches = re.findall(r"Message:\s*(.+?)(?=\\n\}|\n\}|$)", message)
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in matches:
        text = raw.strip().strip("'\"")
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    return cleaned
