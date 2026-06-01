"""Tests for :mod:`agentops.utils.azure_endpoints`."""

from __future__ import annotations

import pytest

from agentops.utils.azure_endpoints import (
    derive_openai_endpoint_from_project,
    normalize_azure_openai_endpoint,
)


@pytest.mark.parametrize(
    "raw, expected",
    [
        # Already-clean values are returned untouched (apart from trailing slash).
        ("https://x.openai.azure.com", "https://x.openai.azure.com"),
        ("https://x.openai.azure.com/", "https://x.openai.azure.com"),
        # The portal-style suffix is stripped.
        ("https://x.openai.azure.com/openai/v1", "https://x.openai.azure.com"),
        ("https://x.openai.azure.com/openai/v1/", "https://x.openai.azure.com"),
        ("https://x.openai.azure.com/openai/v1//", "https://x.openai.azure.com"),
        # Other well-known variants.
        ("https://x.openai.azure.com/openai", "https://x.openai.azure.com"),
        ("https://x.openai.azure.com/openai/", "https://x.openai.azure.com"),
        (
            "https://x.openai.azure.com/openai/deployments",
            "https://x.openai.azure.com",
        ),
        # Case-insensitive suffix match.
        ("https://x.openai.azure.com/OpenAI/V1", "https://x.openai.azure.com"),
        # The AI Services proxy host is also accepted.
        (
            "https://aif-prj.cognitiveservices.azure.com/openai/v1",
            "https://aif-prj.cognitiveservices.azure.com",
        ),
        # Whitespace is trimmed.
        ("  https://x.openai.azure.com/openai/v1  ", "https://x.openai.azure.com"),
    ],
)
def test_normalize_strips_known_suffixes(raw: str, expected: str) -> None:
    assert normalize_azure_openai_endpoint(raw) == expected


def test_normalize_passes_through_none() -> None:
    assert normalize_azure_openai_endpoint(None) is None


def test_normalize_passes_through_empty_string() -> None:
    assert normalize_azure_openai_endpoint("") == ""
    assert normalize_azure_openai_endpoint("   ") == ""


def test_normalize_only_strips_at_the_end() -> None:
    # A path containing "openai" earlier in the URL must not be touched.
    raw = "https://proxy.example.com/openai/v1/extra"
    assert normalize_azure_openai_endpoint(raw) == raw.rstrip("/")


@pytest.mark.parametrize(
    "raw, expected",
    [
        # Canonical Foundry project endpoint (services.ai.azure.com host).
        (
            "https://ai-account-xyz.services.ai.azure.com/api/projects/proj-default",
            "https://ai-account-xyz.services.ai.azure.com",
        ),
        # Trailing slash on the project segment.
        (
            "https://ai-account-xyz.services.ai.azure.com/api/projects/proj-default/",
            "https://ai-account-xyz.services.ai.azure.com",
        ),
        # Legacy cognitiveservices host.
        (
            "https://acct.cognitiveservices.azure.com/api/projects/p1",
            "https://acct.cognitiveservices.azure.com",
        ),
        # Project names may include hyphens and digits.
        (
            "https://acct.services.ai.azure.com/api/projects/travel-agent-sandbox",
            "https://acct.services.ai.azure.com",
        ),
        # Case-insensitive match on the ``/api/projects/`` segment.
        (
            "https://acct.services.ai.azure.com/API/Projects/p1",
            "https://acct.services.ai.azure.com",
        ),
        # Already a base URL — passed through unchanged (apart from trailing slash).
        (
            "https://acct.services.ai.azure.com",
            "https://acct.services.ai.azure.com",
        ),
        # Already a base URL with the OpenAI inference suffix — still normalized.
        (
            "https://acct.services.ai.azure.com/openai/v1",
            "https://acct.services.ai.azure.com",
        ),
    ],
)
def test_derive_openai_endpoint_from_project(raw: str, expected: str) -> None:
    assert derive_openai_endpoint_from_project(raw) == expected


def test_derive_openai_endpoint_from_project_passes_through_none() -> None:
    assert derive_openai_endpoint_from_project(None) is None


def test_derive_openai_endpoint_from_project_passes_through_empty() -> None:
    assert derive_openai_endpoint_from_project("") == ""
    assert derive_openai_endpoint_from_project("   ") == ""


def test_derive_openai_endpoint_from_project_keeps_extra_path() -> None:
    # Only the final ``/api/projects/<name>`` segment is trimmed; an endpoint
    # carrying additional sub-paths (e.g. an explicit agent path) is left as-is.
    raw = "https://acct.services.ai.azure.com/api/projects/proj/agents/foo"
    assert derive_openai_endpoint_from_project(raw) == raw.rstrip("/")
