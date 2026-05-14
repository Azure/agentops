"""Helpers for normalizing Azure OpenAI / AI Services endpoint URLs.

Users commonly copy the Azure OpenAI endpoint straight from the Foundry
portal, which displays it with the inference-path suffix appended
(``https://<resource>.openai.azure.com/openai/v1``). The
``azure-ai-evaluation`` SDK and the ``openai`` SDK both want the
*base* endpoint without that suffix, and would otherwise produce a
confusing ``404`` or ``ResourceNotFound`` error.

This module strips the well-known inference-path suffixes so the user
can paste whichever URL the portal showed and have AgentOps work
transparently.
"""

from __future__ import annotations

import re
from typing import Optional

# Ordered from most- to least-specific so the longest match wins.
# The match is case-insensitive and tolerant of trailing slashes.
_KNOWN_SUFFIXES = (
    "/openai/v1",
    "/openai/deployments",
    "/openai",
)


_SUFFIX_RE = re.compile(
    r"(" + "|".join(re.escape(s) for s in _KNOWN_SUFFIXES) + r")/*\Z",
    re.IGNORECASE,
)


def normalize_azure_openai_endpoint(value: Optional[str]) -> Optional[str]:
    """Return ``value`` with well-known inference-path suffixes removed.

    Examples
    --------
    ``https://x.openai.azure.com/openai/v1`` -> ``https://x.openai.azure.com``
    ``https://x.openai.azure.com/openai/``  -> ``https://x.openai.azure.com``
    ``https://x.openai.azure.com``           -> ``https://x.openai.azure.com``
    ``None`` / ``""``                        -> returned unchanged

    The helper is deliberately conservative:

    * Only the exact suffixes in :data:`_KNOWN_SUFFIXES` are stripped.
    * The scheme, host, port, and any earlier path segments are
      preserved exactly.
    * The result has no trailing slash so downstream SDKs build
      consistent URLs.
    """
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return stripped
    # Drop the known suffix when it appears at the very end of the URL.
    rewritten = _SUFFIX_RE.sub("", stripped)
    # Trim any straggling trailing slash so callers building paths
    # never get a doubled ``//``.
    return rewritten.rstrip("/")
