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


# Foundry project endpoints look like
# ``https://<account>.services.ai.azure.com/api/projects/<project>`` (or the
# legacy ``cognitiveservices.azure.com`` host). The Azure OpenAI inference
# endpoint that the ``openai`` and ``azure-ai-evaluation`` SDKs want is the
# *account* base URL — i.e. the same scheme/host with the project path
# trimmed off. ``derive_openai_endpoint_from_project`` performs that trim so
# users who only configure ``AZURE_AI_FOUNDRY_PROJECT_ENDPOINT`` (the value
# the ``agentops init`` wizard already writes) get a working AI-assisted
# evaluator run without having to hand-author ``AZURE_OPENAI_ENDPOINT`` too.
_PROJECT_PATH_RE = re.compile(r"/api/projects/[^/?#]+/*\Z", re.IGNORECASE)


def derive_openai_endpoint_from_project(value: Optional[str]) -> Optional[str]:
    """Return the Azure OpenAI base URL embedded in a Foundry project endpoint.

    The function is conservative: it only rewrites URLs whose final path
    segment matches ``/api/projects/<project>`` exactly. Anything else
    (already-base URLs, URLs with extra path segments after the project
    name, non-Foundry hosts) is returned untouched apart from a normalizing
    pass through :func:`normalize_azure_openai_endpoint`. ``None`` and
    empty strings pass through unchanged so callers can keep the
    ``os.getenv`` ergonomic of ``derive_openai_endpoint_from_project(os.getenv(...))``.
    """
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return stripped
    trimmed = _PROJECT_PATH_RE.sub("", stripped)
    return normalize_azure_openai_endpoint(trimmed)
