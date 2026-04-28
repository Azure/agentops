"""Target invocation backends for AgentOps 1.0.

Each backend is a single function with the signature::

    invoke(
        target: TargetResolution,
        config: AgentOpsConfig,
        row: dict[str, Any],
        *,
        timeout: float,
    ) -> InvocationResult

The orchestrator dispatches based on :attr:`TargetResolution.kind`. All Azure
SDK imports are lazy so the package imports without optional dependencies.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from agentops.core.agentops_config import AgentOpsConfig, TargetResolution


@dataclass
class InvocationResult:
    """Outcome of invoking the target on one dataset row."""

    response: str
    latency_seconds: float
    tool_calls: Optional[List[Any]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _credential() -> Any:
    from azure.identity import DefaultAzureCredential  # noqa: WPS433

    return DefaultAzureCredential(exclude_developer_cli_credential=True)


def _get_token(scope: str) -> str:
    return _credential().get_token(scope).token


def _project_endpoint_from_env() -> str:
    endpoint = os.getenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT")
    if not endpoint:
        raise RuntimeError(
            "Missing AZURE_AI_FOUNDRY_PROJECT_ENDPOINT environment variable. "
            "Foundry targets require a project endpoint URL."
        )
    return endpoint.rstrip("/")


def _row_input(row: Dict[str, Any]) -> str:
    value = row.get("input")
    if value is None:
        raise ValueError("dataset row is missing required 'input' field")
    return str(value)


def _http_request_json(
    *,
    method: str,
    url: str,
    headers: Dict[str, str],
    body: Optional[Dict[str, Any]] = None,
    timeout: float,
) -> Dict[str, Any]:
    encoded = json.dumps(body or {}).encode("utf-8") if method != "GET" else None
    request = urllib.request.Request(
        url=url, data=encoded, method=method, headers=headers
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            payload = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        raise RuntimeError(
            f"HTTP {exc.code} from {url}: {detail or exc.reason}"
        ) from exc
    if not payload:
        return {}
    return json.loads(payload)


def _dot_path(payload: Any, path: str) -> Any:
    """Resolve ``a.b.c`` or ``a.0.b`` against a JSON-like object."""
    current = payload
    for token in path.split("."):
        if current is None:
            return None
        if isinstance(current, list):
            try:
                current = current[int(token)]
            except (ValueError, IndexError):
                return None
            continue
        if isinstance(current, dict):
            current = current.get(token)
            continue
        return None
    return current


def _extract_responses_text(payload: Dict[str, Any]) -> str:
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    output = payload.get("output")
    tool_call_summaries: List[str] = []
    if isinstance(output, list):
        parts: List[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            if item.get("type") in {"tool_call", "function_call"}:
                name = item.get("name") or "tool"
                args = item.get("arguments")
                tool_call_summaries.append(f"[Called {name}({args})]" if args else f"[Called {name}]")
                continue
            if (
                item.get("type") in {"message", "assistant_message"}
                or item.get("role") == "assistant"
            ):
                content = item.get("content")
                if isinstance(content, str):
                    parts.append(content)
                elif isinstance(content, list):
                    for chunk in content:
                        if isinstance(chunk, dict):
                            text = chunk.get("text") or chunk.get("output_text")
                            if isinstance(text, str):
                                parts.append(text)
                        elif isinstance(chunk, str):
                            parts.append(chunk)
        if parts:
            return "\n".join(parts).strip()
        if tool_call_summaries:
            # Agent invoked tool(s) instead of replying with text. Return a
            # synthetic, human-readable summary so quality evaluators (which
            # require non-empty response text) still run; tool-use evaluators
            # use the structured tool_calls field separately.
            return " ".join(tool_call_summaries)

    raise ValueError("Foundry response did not include assistant output text")


def _extract_responses_tool_calls(payload: Dict[str, Any]) -> Optional[List[Any]]:
    output = payload.get("output")
    if not isinstance(output, list):
        return None
    calls: List[Any] = []
    for item in output:
        if isinstance(item, dict) and item.get("type") in {
            "tool_call",
            "function_call",
        }:
            calls.append(item)
    return calls or None


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------


def _invoke_model_direct(
    target: TargetResolution,
    config: AgentOpsConfig,  # noqa: ARG001
    row: Dict[str, Any],
    *,
    timeout: float,  # noqa: ARG001
) -> InvocationResult:
    from azure.ai.projects import AIProjectClient  # noqa: WPS433

    project_endpoint = _project_endpoint_from_env()
    client = AIProjectClient(endpoint=project_endpoint, credential=_credential())
    openai_client = client.get_openai_client()

    assert target.deployment is not None
    started = time.perf_counter()
    response = openai_client.chat.completions.create(
        model=target.deployment,
        messages=[{"role": "user", "content": _row_input(row)}],
    )
    elapsed = time.perf_counter() - started

    text = ""
    if response.choices:
        message = response.choices[0].message
        if message and message.content:
            text = message.content.strip()
    if not text:
        raise RuntimeError("model_direct invocation returned empty content")

    return InvocationResult(response=text, latency_seconds=elapsed)


def _invoke_foundry_prompt(
    target: TargetResolution,
    config: AgentOpsConfig,  # noqa: ARG001
    row: Dict[str, Any],
    *,
    timeout: float,
) -> InvocationResult:
    project_endpoint = _project_endpoint_from_env()
    token = _get_token("https://ai.azure.com/.default")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }

    assert target.name is not None and target.version is not None
    body = {
        "input": [{"role": "user", "content": _row_input(row)}],
        "agent_reference": {
            "type": "agent_reference",
            "name": target.name,
            "version": target.version,
        },
    }

    started = time.perf_counter()
    payload = _http_request_json(
        method="POST",
        url=f"{project_endpoint}/openai/v1/responses",
        headers=headers,
        body=body,
        timeout=timeout,
    )
    elapsed = time.perf_counter() - started

    text = _extract_responses_text(payload)
    tool_calls = _extract_responses_tool_calls(payload)
    return InvocationResult(
        response=text, latency_seconds=elapsed, tool_calls=tool_calls,
    )


def _invoke_foundry_hosted(
    target: TargetResolution,
    config: AgentOpsConfig,
    row: Dict[str, Any],
    *,
    timeout: float,
) -> InvocationResult:
    assert target.url is not None
    token = _get_token("https://ai.azure.com/.default")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
        **config.headers,
    }

    if target.protocol == "responses":
        body = {"input": [{"role": "user", "content": _row_input(row)}]}
        url = target.url.rstrip("/")
        if not url.endswith("/responses"):
            url = f"{url}/responses"

        started = time.perf_counter()
        payload = _http_request_json(
            method="POST", url=url, headers=headers, body=body, timeout=timeout
        )
        elapsed = time.perf_counter() - started

        return InvocationResult(
            response=_extract_responses_text(payload),
            latency_seconds=elapsed,
            tool_calls=_extract_responses_tool_calls(payload),
        )

    return _invoke_http_json(target, config, row, timeout=timeout)


def _invoke_http_json(
    target: TargetResolution,
    config: AgentOpsConfig,
    row: Dict[str, Any],
    *,
    timeout: float,
) -> InvocationResult:
    assert target.url is not None
    headers: Dict[str, str] = {"Content-Type": "application/json", **config.headers}
    if config.auth_header_env:
        token = os.getenv(config.auth_header_env)
        if not token:
            raise RuntimeError(
                f"auth_header_env {config.auth_header_env!r} is set in config but "
                "the environment variable is empty"
            )
        headers["Authorization"] = f"Bearer {token}"

    request_field = config.request_field or "message"
    body: Dict[str, Any] = {request_field: _row_input(row)}

    started = time.perf_counter()
    payload = _http_request_json(
        method="POST",
        url=target.url,
        headers=headers,
        body=body,
        timeout=timeout,
    )
    elapsed = time.perf_counter() - started

    response_path = config.response_field or "text"
    response_text = _dot_path(payload, response_path)
    if response_text is None:
        for fallback in ("response", "output", "content", "message", "text"):
            response_text = payload.get(fallback)
            if response_text:
                break
    if response_text is None:
        raise ValueError(
            f"HTTP/JSON response did not contain field {response_path!r}; "
            f"got top-level keys: {sorted(payload.keys())}"
        )
    if not isinstance(response_text, str):
        response_text = json.dumps(response_text, ensure_ascii=False)

    tool_calls: Optional[List[Any]] = None
    if config.tool_calls_field:
        extracted = _dot_path(payload, config.tool_calls_field)
        if isinstance(extracted, list):
            tool_calls = extracted

    return InvocationResult(
        response=response_text.strip(),
        latency_seconds=elapsed,
        tool_calls=tool_calls,
    )


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def invoke(
    target: TargetResolution,
    config: AgentOpsConfig,
    row: Dict[str, Any],
    *,
    timeout: float,
) -> InvocationResult:
    """Dispatch to the right backend based on the resolved target kind."""
    if target.kind == "model_direct":
        return _invoke_model_direct(target, config, row, timeout=timeout)
    if target.kind == "foundry_prompt":
        return _invoke_foundry_prompt(target, config, row, timeout=timeout)
    if target.kind == "foundry_hosted":
        return _invoke_foundry_hosted(target, config, row, timeout=timeout)
    if target.kind == "http_json":
        return _invoke_http_json(target, config, row, timeout=timeout)
    raise ValueError(f"unknown target kind: {target.kind}")
