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
from agentops.pipeline.diagnostics import with_tenant_mismatch_guidance


@dataclass
class InvocationResult:
    """Outcome of invoking the target on one dataset row."""

    response: str
    latency_seconds: float
    tool_calls: Optional[List[Any]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


# Maximum number of follow-up calls when running the tool-execution loop
# against a Foundry hosted/prompt agent. Most agents resolve in 1–2 hops;
# the cap exists to bound retries against pathological multi-step plans.
_MAX_TOOL_ITERATIONS = 4

# Generic stub returned to the agent for every function call during the
# tool-execution loop. The toolkit cannot run project-specific tool
# implementations, so a uniform "ok" stub keeps the loop fully generic
# while letting the agent produce its final natural-language reply.
_TOOL_STUB_OUTPUT = '{"status": "ok"}'


def _summarise_tool_calls(calls: List[Any]) -> str:
    """Build a short, human-readable summary of executed tool calls.

    Used as a last-resort fallback when the agent never produces
    assistant text - quality evaluators still need a non-empty
    ``response`` string to score.
    """
    parts: List[str] = []
    for call in calls:
        if not isinstance(call, dict):
            continue
        name = call.get("name") or "tool"
        args = call.get("arguments")
        parts.append(f"[Called {name}({args})]" if args else f"[Called {name}]")
    return " ".join(parts) if parts else "[tool_call]"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _credential() -> Any:
    """Return a cached ``DefaultAzureCredential`` singleton.

    Caching matters: each row invocation needs a token, and constructing a
    fresh ``DefaultAzureCredential`` per call walks the full credential
    chain (Azure CLI / PowerShell subprocesses included), which is both
    slow and prone to transient subprocess failures on the first try.
    Caching the credential lets the SDK reuse its internal token cache
    across rows.
    """
    global _CREDENTIAL_SINGLETON
    if _CREDENTIAL_SINGLETON is None:
        from azure.identity import DefaultAzureCredential  # noqa: WPS433

        _CREDENTIAL_SINGLETON = DefaultAzureCredential(exclude_developer_cli_credential=True, process_timeout=30)
    return _CREDENTIAL_SINGLETON


_CREDENTIAL_SINGLETON: Any = None


def _get_token(scope: str) -> str:
    """Acquire a token for ``scope``, retrying once on transient failures.

    The first credential-chain walk on Windows occasionally fails because
    the Azure CLI / PowerShell subprocess is slow to spawn. Retrying once
    after the credential is warmed up almost always succeeds.
    """
    try:
        return _credential().get_token(scope).token
    except Exception:  # noqa: BLE001 - retry once on any transient failure
        global _CREDENTIAL_SINGLETON
        _CREDENTIAL_SINGLETON = None  # force a fresh chain walk
        return _credential().get_token(scope).token


def _project_endpoint(config: AgentOpsConfig) -> str:
    endpoint = config.project_endpoint or os.getenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT")
    if not endpoint:
        raise RuntimeError(
            "Foundry targets require a project endpoint URL. Set "
            "'project_endpoint' in agentops.yaml or set the "
            "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT environment variable."
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
    last_exc: Optional[BaseException] = None
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                payload = response.read().decode("utf-8")
            break
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            transient = exc.code >= 500 or exc.code == 429
            if transient and attempt < 3:
                time.sleep(2 ** attempt)
                last_exc = exc
                continue
            message = f"HTTP {exc.code} from {url}: {detail or exc.reason}"
            raise RuntimeError(with_tenant_mismatch_guidance(message)) from exc
        except urllib.error.URLError as exc:
            if attempt < 3:
                time.sleep(2 ** attempt)
                last_exc = exc
                continue
            raise
    else:  # pragma: no cover - loop exits via break/raise
        raise RuntimeError(f"HTTP request to {url} failed: {last_exc!r}")
    if not payload:
        return {}
    return json.loads(payload)


def _http_request_stream(
    *,
    method: str,
    url: str,
    headers: Dict[str, str],
    body: Optional[Dict[str, Any]] = None,
    timeout: float,
) -> str:
    """POST and return the full streamed response body as decoded text.

    Uses the same 3-try backoff policy and tenant-mismatch guidance as
    :func:`_http_request_json`. The response is read to completion (streamed
    endpoints used for evaluation emit a bounded answer) and returned verbatim
    so :func:`_aggregate_stream` can reassemble it. stdlib ``urllib`` only.
    """
    encoded = json.dumps(body or {}).encode("utf-8") if method != "GET" else None
    request = urllib.request.Request(
        url=url, data=encoded, method=method, headers=headers
    )
    last_exc: Optional[BaseException] = None
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                # HTTPResponse is iterable line-by-line; joining restores the
                # full body (including newlines) for both sse and text modes.
                chunks = [line.decode("utf-8", errors="replace") for line in response]
            return "".join(chunks)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            transient = exc.code >= 500 or exc.code == 429
            if transient and attempt < 3:
                time.sleep(2 ** attempt)
                last_exc = exc
                continue
            message = f"HTTP {exc.code} from {url}: {detail or exc.reason}"
            raise RuntimeError(with_tenant_mismatch_guidance(message)) from exc
        except urllib.error.URLError as exc:
            if attempt < 3:
                time.sleep(2 ** attempt)
                last_exc = exc
                continue
            raise
    else:  # pragma: no cover - loop exits via break/raise
        raise RuntimeError(f"HTTP request to {url} failed: {last_exc!r}")


def _strip_leading_token(text: str) -> str:
    """Drop the first whitespace-delimited token from ``text``.

    Used to remove the conversation-id prefix the gpt-rag orchestrator emits
    as its first streamed chunk (``"<conversation_id> <answer...>"``).
    """
    stripped = text.strip()
    parts = stripped.split(None, 1)
    return parts[1] if len(parts) == 2 else ""


def _aggregate_stream(
    response_mode: str,
    body: str,
    stream_cfg: Optional[Any],
) -> str:
    """Reassemble a streamed response body into a single answer string.

    ``text`` mode concatenates the whole body. ``sse`` mode parses ``data:``
    lines, optionally JSON-decoding each line and extracting ``text_field``,
    stopping at ``done_marker``, and raising on an ``event: error`` frame.
    """
    strip_leading = bool(getattr(stream_cfg, "strip_leading_token", False))

    if response_mode == "text":
        text = body or ""
        return _strip_leading_token(text) if strip_leading else text

    # SSE mode.
    text_field = getattr(stream_cfg, "text_field", None)
    done_marker = getattr(stream_cfg, "done_marker", None)

    pieces: List[str] = []
    saw_error = False
    for raw_line in (body or "").splitlines():
        line = raw_line.rstrip("\r")
        stripped = line.strip()
        if not stripped:
            # Blank line closes the current SSE frame.
            saw_error = False
            continue
        if stripped.startswith("event:"):
            if stripped[len("event:"):].strip() == "error":
                saw_error = True
            continue
        if not stripped.startswith("data:"):
            # Ignore id:/retry:/comment lines.
            continue
        data = line.split("data:", 1)[1]
        if data.startswith(" "):
            # SSE strips a single leading space after the field colon.
            data = data[1:]
        if done_marker is not None and data == done_marker:
            break
        if saw_error:
            raise RuntimeError(
                f"streaming endpoint returned an error event: {data}"
            )
        if text_field:
            try:
                parsed = json.loads(data)
            except json.JSONDecodeError:
                pieces.append(data)
                continue
            token = _dot_path(parsed, text_field)
            if token is None:
                continue
            pieces.append(
                token if isinstance(token, str)
                else json.dumps(token, ensure_ascii=False)
            )
        else:
            pieces.append(data)

    text = "".join(pieces)
    return _strip_leading_token(text) if strip_leading else text


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
    """Pull assistant text from a Foundry/Responses-API payload.

    Returns an empty string when the response only contains tool/function
    calls (the caller must submit ``function_call_output`` items via a
    follow-up call to obtain the final natural-language reply).
    """
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    output = payload.get("output")
    if isinstance(output, list):
        parts: List[str] = []
        for item in output:
            if not isinstance(item, dict):
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
        return ""

    return ""


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
    config: AgentOpsConfig,
    row: Dict[str, Any],
    *,
    timeout: float,  # noqa: ARG001
) -> InvocationResult:
    from azure.ai.projects import AIProjectClient  # noqa: WPS433

    project_endpoint = _project_endpoint(config)
    client = AIProjectClient(endpoint=project_endpoint, credential=_credential())
    openai_client = client.get_openai_client()

    assert target.deployment is not None
    started = time.perf_counter()
    last_exc: Optional[BaseException] = None
    response = None
    for attempt in range(1, 4):
        try:
            response = openai_client.chat.completions.create(
                model=target.deployment,
                messages=[{"role": "user", "content": _row_input(row)}],
            )
            break
        except Exception as exc:  # noqa: BLE001
            status = getattr(exc, "status_code", None)
            transient = status is None or status >= 500 or status == 429
            if transient and attempt < 3:
                time.sleep(2 ** attempt)
                last_exc = exc
                continue
            raise
    if response is None:
        raise RuntimeError(f"model_direct invocation failed after retries: {last_exc!r}")
    elapsed = time.perf_counter() - started

    text = ""
    if response.choices:
        message = response.choices[0].message
        if message and message.content:
            text = message.content.strip()
    if not text:
        raise RuntimeError("model_direct invocation returned empty content")

    return InvocationResult(response=text, latency_seconds=elapsed)


def _run_responses_tool_loop(
    *,
    url: str,
    headers: Dict[str, str],
    initial_body: Dict[str, Any],
    timeout: float,
    follow_up_extras: Optional[Dict[str, Any]] = None,
) -> tuple[str, List[Any], float]:
    """Drive a Foundry/Responses-API tool-execution loop.

    Sends ``initial_body`` to ``url``, then repeatedly submits stub
    ``function_call_output`` items back via ``previous_response_id`` until
    the agent emits assistant text or the iteration cap is reached.

    ``follow_up_extras`` is merged into every follow-up request body
    (e.g. ``agent_reference`` for prompt agents).

    Returns ``(text, aggregated_tool_calls, elapsed_seconds)``.
    """
    started = time.perf_counter()
    aggregated_tool_calls: List[Any] = []
    text = ""
    body = initial_body

    for _iteration in range(_MAX_TOOL_ITERATIONS):
        payload = _http_request_json(
            method="POST",
            url=url,
            headers=headers,
            body=body,
            timeout=timeout,
        )

        iteration_calls = _extract_responses_tool_calls(payload) or []
        aggregated_tool_calls.extend(iteration_calls)

        text = _extract_responses_text(payload)
        if text or not iteration_calls:
            break

        previous_response_id = payload.get("id")
        follow_up_input: List[Dict[str, Any]] = []
        for call in iteration_calls:
            call_id = call.get("call_id") or call.get("id")
            if not call_id:
                continue
            follow_up_input.append(
                {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": _TOOL_STUB_OUTPUT,
                }
            )

        if not follow_up_input or not previous_response_id:
            break

        body = {
            "input": follow_up_input,
            "previous_response_id": previous_response_id,
        }
        if follow_up_extras:
            body.update(follow_up_extras)

    elapsed = time.perf_counter() - started
    return text, aggregated_tool_calls, elapsed


def _invoke_foundry_prompt(
    target: TargetResolution,
    config: AgentOpsConfig,
    row: Dict[str, Any],
    *,
    timeout: float,
) -> InvocationResult:
    project_endpoint = _project_endpoint(config)
    token = _get_token("https://ai.azure.com/.default")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }

    assert target.name is not None and target.version is not None
    url = f"{project_endpoint}/openai/v1/responses"
    agent_reference = {
        "type": "agent_reference",
        "name": target.name,
        "version": target.version,
    }
    initial_body: Dict[str, Any] = {
        "input": [{"role": "user", "content": _row_input(row)}],
        "agent_reference": agent_reference,
    }

    text, aggregated_tool_calls, elapsed = _run_responses_tool_loop(
        url=url,
        headers=headers,
        initial_body=initial_body,
        timeout=timeout,
        follow_up_extras={"agent_reference": agent_reference},
    )

    if not text:
        if aggregated_tool_calls:
            text = _summarise_tool_calls(aggregated_tool_calls)
        else:
            raise ValueError(
                "Foundry response did not include assistant output text"
            )

    return InvocationResult(
        response=text,
        latency_seconds=elapsed,
        tool_calls=aggregated_tool_calls or None,
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
        url = target.url.rstrip("/")
        if not url.endswith("/responses"):
            url = f"{url}/responses"
        initial_body = {"input": [{"role": "user", "content": _row_input(row)}]}

        text, aggregated_tool_calls, elapsed = _run_responses_tool_loop(
            url=url,
            headers=headers,
            initial_body=initial_body,
            timeout=timeout,
        )

        if not text:
            if aggregated_tool_calls:
                text = _summarise_tool_calls(aggregated_tool_calls)
            else:
                raise ValueError(
                    "Foundry response did not include assistant output text"
                )

        return InvocationResult(
            response=text,
            latency_seconds=elapsed,
            tool_calls=aggregated_tool_calls or None,
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
        # Default to today's behavior (Authorization: Bearer <token>) so
        # existing configs are byte-for-byte unchanged; allow an arbitrary
        # header name/value template for shared-secret gates (e.g. X-API-KEY).
        header_name = config.auth_header_name or "Authorization"
        value_template = config.auth_value_template or "Bearer {token}"
        headers[header_name] = value_template.replace("{token}", token)

    request_field = config.request_field or "message"
    body: Dict[str, Any] = {request_field: _row_input(row)}

    if config.response_mode in ("sse", "text"):
        started = time.perf_counter()
        raw_body = _http_request_stream(
            method="POST",
            url=target.url,
            headers=headers,
            body=body,
            timeout=timeout,
        )
        elapsed = time.perf_counter() - started
        aggregated = _aggregate_stream(config.response_mode, raw_body, config.stream)
        return InvocationResult(
            response=aggregated.strip(),
            latency_seconds=elapsed,
            tool_calls=None,
        )

    started = time.perf_counter()
    try:
        payload = _http_request_json(
            method="POST",
            url=target.url,
            headers=headers,
            body=body,
            timeout=timeout,
        )
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"HTTP/JSON response from {target.url} was not valid JSON. If this "
            "endpoint streams Server-Sent Events or raw text (for example "
            "Content-Type: text/event-stream), set response_mode: sse or "
            "response_mode: text in agentops.yaml."
        ) from exc
    elapsed = time.perf_counter() - started

    response_path = config.response_field or config.response_fields.get("response") or "text"
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

    response_fields: Dict[str, Any] = {}
    for name, path in config.response_fields.items():
        value = _dot_path(payload, path)
        if value is not None:
            response_fields[name] = value

    tool_calls: Optional[List[Any]] = None
    if config.tool_calls_field:
        extracted = _dot_path(payload, config.tool_calls_field)
        if isinstance(extracted, list):
            tool_calls = extracted

    return InvocationResult(
        response=response_text.strip(),
        latency_seconds=elapsed,
        tool_calls=tool_calls,
        metadata={"response_fields": response_fields} if response_fields else {},
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
