"""Native Microsoft Foundry Agent Service backend implementation for AgentOps."""

from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Dict, List

from agentops.backends.base import BackendExecutionResult, BackendRunContext
from agentops.backends.eval_engine import (
    _CREDENTIAL_HELP_MESSAGE,
    _NLP_DEFAULT_INIT_PARAMS,
    _build_foundry_evaluator_runtimes,
    _cloud_evaluator_data_mapping,
    _cloud_evaluator_needs_model,
    _load_jsonl,
    _normalize_text,
    _parse_agent_name_version,
    _resolve_dataset_source_path,
    _run_foundry_evaluator,
    _to_builtin_evaluator_name,
    _validate_supported_local_evaluators,
)
from agentops.core.config_loader import load_bundle_config, load_dataset_config

logger = logging.getLogger(__name__)


def _to_utc_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Cloud evaluation routing
# ---------------------------------------------------------------------------


def _should_use_cloud_evaluation(project_endpoint: str) -> bool:
    """Return True when cloud evaluation should be used (New Foundry Experience)."""
    mode = os.getenv("AGENTOPS_FOUNDRY_MODE", "cloud").strip().lower()
    if mode in {"local", "legacy"}:
        return False
    if "example.services.ai.azure.com" in project_endpoint:
        return False
    return True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _acquire_token(scope: str) -> str:
    """Acquire a bearer token for the Foundry Agent Service.

    Uses ``DefaultAzureCredential`` which supports:
    - Local dev: ``az login`` / VS Code credential
    - CI/CD: service principal (``AZURE_CLIENT_ID``, ``AZURE_TENANT_ID``,
      ``AZURE_CLIENT_SECRET``)
    - Azure hosted: managed identity (zero config)
    """
    try:
        from azure.identity import DefaultAzureCredential  # noqa: WPS433
    except ImportError as exc:
        raise ImportError(
            "Foundry backend requires 'azure-identity'.  "
            "Install with:  pip install azure-identity"
        ) from exc

    try:
        credential = DefaultAzureCredential(exclude_developer_cli_credential=True)
        token = credential.get_token(scope)
        return token.token
    except Exception as exc:
        # Catch ClientAuthenticationError and any other credential failures
        # and re-raise with a clean, actionable message.
        raise RuntimeError(_CREDENTIAL_HELP_MESSAGE) from exc


def _preferred_scope_for_agent_id(agent_id: str) -> str:
    if agent_id.startswith("asst_"):
        return "https://cognitiveservices.azure.com/.default"
    return "https://ai.azure.com/.default"


def _alternate_scope(scope: str) -> str:
    if scope == "https://ai.azure.com/.default":
        return "https://cognitiveservices.azure.com/.default"
    return "https://ai.azure.com/.default"


def _is_audience_mismatch(details: str) -> bool:
    lowered = details.lower()
    if "audience is incorrect" in lowered:
        return True
    return bool(re.search(r"audience.*(incorrect|invalid)", lowered))


@dataclass(frozen=True)
class FoundrySettings:
    project_endpoint: str
    agent_id: str | None
    model: str | None
    api_version: str
    agent_token: str
    token_scope: str
    poll_interval_seconds: float
    max_poll_attempts: int
    target: str = "agent"  # 'agent' or 'model'


def _derive_openai_endpoint_from_project(project_endpoint: str) -> str:
    """Derive the Azure OpenAI base endpoint from a Foundry project endpoint.

    ``https://account.services.ai.azure.com/api/projects/proj``
    → ``https://account.services.ai.azure.com/``
    """
    from urllib.parse import urlparse  # noqa: WPS433

    parsed = urlparse(project_endpoint)
    return f"{parsed.scheme}://{parsed.netloc}/"


class FoundryBackend:
    def _read_settings(self, context: BackendRunContext) -> FoundrySettings:
        target_cfg = context.run_config.target
        endpoint = target_cfg.endpoint
        assert endpoint is not None, "Foundry backend requires target.endpoint"

        project_endpoint_env = (
            endpoint.project_endpoint_env or "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT"
        )

        project_endpoint = endpoint.project_endpoint or os.getenv(project_endpoint_env)
        agent_id = endpoint.agent_id
        target = target_cfg.type  # "agent" or "model"
        model = endpoint.model or os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME")
        api_version = endpoint.api_version or "2025-05-01"

        if not project_endpoint:
            raise ValueError(
                f"Foundry backend requires a project endpoint. Set it via:\n"
                f"\n"
                f"  1. 'target.endpoint.project_endpoint' in your run.yaml, or\n"
                f"  2. Environment variable {project_endpoint_env}:\n"
                f"\n"
                f"     PowerShell:\n"
                f'       $env:{project_endpoint_env} = "https://<account>.services.ai.azure.com/api/projects/<project>"\n'
                f"\n"
                f"     Bash/zsh:\n"
                f'       export {project_endpoint_env}="https://<account>.services.ai.azure.com/api/projects/<project>"\n'
                f"\n"
                f"You can find this URL in the Azure AI Foundry portal under your project settings."
            )
        if target == "agent" and not agent_id:
            raise ValueError(
                "Foundry backend requires target.endpoint.agent_id when target type is 'agent'"
            )
        if target == "model" and not model:
            raise ValueError(
                "Foundry backend requires a model deployment name when target type is 'model'. "
                "Set 'target.endpoint.model' in run.yaml or AZURE_AI_MODEL_DEPLOYMENT_NAME."
            )

        if target == "model":
            # Model-direct: use cognitive services scope
            token_scope = "https://cognitiveservices.azure.com/.default"
        else:
            token_scope = _preferred_scope_for_agent_id(agent_id)
        logger.info("Acquiring token via DefaultAzureCredential…")
        agent_token = _acquire_token(token_scope)

        return FoundrySettings(
            project_endpoint=project_endpoint.rstrip("/"),
            agent_id=agent_id,
            model=model,
            api_version=api_version,
            agent_token=agent_token,
            token_scope=token_scope,
            poll_interval_seconds=endpoint.poll_interval_seconds or 2.0,
            max_poll_attempts=endpoint.max_poll_attempts or 120,
            target=target,
        )

    def _request_json(
        self,
        *,
        method: str,
        url: str,
        headers: Dict[str, str],
        timeout_seconds: int | None,
        body: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        request_body = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(
            url=url,
            method=method,
            data=request_body,
            headers=headers,
        )

        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))

        if not isinstance(payload, dict):
            raise ValueError(
                "Invalid Foundry Agent Service response: expected JSON object"
            )
        return payload

    def _extract_agent_message_text(self, messages_payload: Dict[str, Any]) -> str:
        entries = messages_payload.get("data")
        if not isinstance(entries, list):
            raise ValueError(
                "Invalid Foundry Agent Service response: missing messages data"
            )

        for message in entries:
            if not isinstance(message, dict) or message.get("role") != "assistant":
                continue

            content = message.get("content")
            if isinstance(content, str):
                return content.strip()

            if isinstance(content, list):
                parts: List[str] = []
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    text_payload = item.get("text")
                    if isinstance(text_payload, dict):
                        value = text_payload.get("value")
                        if isinstance(value, str):
                            parts.append(value)
                    elif isinstance(item.get("value"), str):
                        parts.append(item["value"])
                if parts:
                    return "\n".join(parts).strip()

        raise ValueError(
            "Invalid Foundry Agent Service response: no assistant message found"
        )

    def _extract_response_output_text(self, response_payload: Dict[str, Any]) -> str:
        output = response_payload.get("output")
        if not isinstance(output, list):
            raise ValueError("Invalid Foundry response payload: missing output array")

        for item in output:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue

            content = item.get("content")
            if not isinstance(content, list):
                continue

            parts: List[str] = []
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "output_text" and isinstance(
                    part.get("text"), str
                ):
                    parts.append(part["text"])

            if parts:
                return "\n".join(parts).strip()

        raise ValueError(
            "Invalid Foundry response payload: no assistant output text found"
        )

    def _invoke_agent_reference(
        self,
        settings: FoundrySettings,
        prompt: str,
        timeout_seconds: int | None,
    ) -> str:
        if not settings.model:
            raise ValueError(
                "Foundry agent reference mode requires a model deployment name. "
                "Set 'backend.model' in run.yaml or AZURE_AI_MODEL_DEPLOYMENT_NAME."
            )

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {settings.agent_token}",
        }

        agent_name, agent_version = (settings.agent_id, None)
        if ":" in settings.agent_id:
            split_name, split_version = settings.agent_id.split(":", 1)
            agent_name = split_name.strip()
            agent_version = split_version.strip() or None

        agent_reference: Dict[str, Any] = {
            "type": "agent_reference",
            "name": agent_name,
        }
        if agent_version:
            agent_reference["version"] = agent_version

        response_payload = self._request_json(
            method="POST",
            url=f"{settings.project_endpoint}/openai/v1/responses",
            headers=headers,
            timeout_seconds=timeout_seconds,
            body={
                "model": settings.model,
                "input": [{"role": "user", "content": prompt}],
                "agent_reference": agent_reference,
            },
        )

        return self._extract_response_output_text(response_payload)

    def _invoke_agent_service(
        self, settings: FoundrySettings, prompt: str, timeout_seconds: int | None
    ) -> str:
        if not settings.agent_id.startswith("asst_"):
            return self._invoke_agent_reference(settings, prompt, timeout_seconds)

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {settings.agent_token}",
        }

        thread_url = (
            f"{settings.project_endpoint}/threads?api-version={settings.api_version}"
        )
        thread_payload = self._request_json(
            method="POST",
            url=thread_url,
            headers=headers,
            timeout_seconds=timeout_seconds,
            body={},
        )
        thread_id = thread_payload.get("id")
        if not isinstance(thread_id, str) or not thread_id:
            raise ValueError(
                "Invalid Foundry Agent Service response: missing thread id"
            )

        message_url = f"{settings.project_endpoint}/threads/{thread_id}/messages?api-version={settings.api_version}"
        self._request_json(
            method="POST",
            url=message_url,
            headers=headers,
            timeout_seconds=timeout_seconds,
            body={"role": "user", "content": prompt},
        )

        run_url = f"{settings.project_endpoint}/threads/{thread_id}/runs?api-version={settings.api_version}"
        run_payload = self._request_json(
            method="POST",
            url=run_url,
            headers=headers,
            timeout_seconds=timeout_seconds,
            body={"assistant_id": settings.agent_id},
        )
        run_id = run_payload.get("id")
        if not isinstance(run_id, str) or not run_id:
            raise ValueError("Invalid Foundry Agent Service response: missing run id")

        status_url = (
            f"{settings.project_endpoint}/threads/{thread_id}/runs/{run_id}"
            f"?api-version={settings.api_version}"
        )

        terminal_success = {"completed"}
        terminal_failure = {"failed", "cancelled", "expired", "requires_action"}

        for _ in range(settings.max_poll_attempts):
            status_payload = self._request_json(
                method="GET",
                url=status_url,
                headers=headers,
                timeout_seconds=timeout_seconds,
            )
            status = status_payload.get("status")
            if isinstance(status, str):
                if status in terminal_success:
                    break
                if status in terminal_failure:
                    raise RuntimeError(
                        f"Foundry agent run ended with status '{status}'"
                    )
            time.sleep(settings.poll_interval_seconds)
        else:
            raise TimeoutError("Timed out waiting for Foundry agent run completion")

        messages_url = f"{settings.project_endpoint}/threads/{thread_id}/messages?api-version={settings.api_version}"
        messages_payload = self._request_json(
            method="GET",
            url=messages_url,
            headers=headers,
            timeout_seconds=timeout_seconds,
        )

        return self._extract_agent_message_text(messages_payload)

    def _invoke_model_direct(self, settings: FoundrySettings, prompt: str) -> str:
        """Call the model deployment directly via the OpenAI chat completions API.

        Used when ``target=model`` — no agent is involved.  The Foundry project
        endpoint is used to derive the Azure OpenAI base URL, and the model
        deployment name comes from ``settings.model``.
        """
        try:
            from azure.ai.projects import AIProjectClient  # noqa: WPS433
            from azure.identity import DefaultAzureCredential  # noqa: WPS433
        except ImportError as exc:
            raise ImportError(
                "Model-direct evaluation requires 'azure-ai-projects>=2.0.1' "
                "and 'azure-identity'. "
                "Install with: pip install 'azure-ai-projects>=2.0.1' azure-identity openai"
            ) from exc

        credential = DefaultAzureCredential(exclude_developer_cli_credential=True)
        project_client = AIProjectClient(
            endpoint=settings.project_endpoint,
            credential=credential,
        )
        openai_client = project_client.get_openai_client()

        response = openai_client.chat.completions.create(
            model=settings.model,
            messages=[{"role": "user", "content": prompt}],
        )

        if response.choices:
            message = response.choices[0].message
            if message and message.content:
                return message.content.strip()

        raise ValueError("Model-direct invocation returned no content")

    def _execute_cloud_evaluation(
        self,
        *,
        context: BackendRunContext,
        settings: FoundrySettings,
        bundle_config: Any,
        dataset_config: Any,
        dataset_source_path: Path,
        started: datetime,
        started_perf: float,
        stdout_path: Path,
        stderr_path: Path,
        metrics_path: Path,
    ) -> BackendExecutionResult:
        """Run evaluation via the Foundry Project Evals API (New Experience).

        Uses the Foundry Project REST endpoint
        ``{project_endpoint}/openai/evals?api-version=2025-11-15-preview``
        with ``azure_ai_evaluator`` testing criteria so results appear in the
        Foundry Evaluations page.

        Reference: https://learn.microsoft.com/azure/foundry/how-to/develop/cloud-evaluation
        """
        # The Foundry Project Evals API version that supports azure_ai_evaluator.
        _EVALS_API_VERSION = "2025-11-15-preview"

        rows = _load_jsonl(dataset_source_path)
        total_rows = len(rows)
        input_field = dataset_config.format.input_field
        expected_field = dataset_config.format.expected_field

        enabled_evaluators = [
            evaluator for evaluator in bundle_config.evaluators if evaluator.enabled
        ]
        _validate_supported_local_evaluators(enabled_evaluators)
        enabled_evaluator_order = [evaluator.name for evaluator in enabled_evaluators]

        foundry_evaluators = [
            evaluator
            for evaluator in enabled_evaluators
            if evaluator.source == "foundry"
        ]
        if not foundry_evaluators:
            raise ValueError(
                "Foundry Cloud Evaluation requires at least one enabled evaluator with source='foundry'"
            )

        logger.info(
            "Starting Foundry Cloud Evaluation for %d dataset row(s) "
            "(target=%s, agent=%s, model=%s, evaluators=%s)",
            total_rows,
            settings.target,
            settings.agent_id or "(none)",
            settings.model,
            [e.name for e in foundry_evaluators],
        )

        # --- Build testing criteria (azure_ai_evaluator) ---------------------
        testing_criteria: List[Dict[str, Any]] = []
        for evaluator in foundry_evaluators:
            builtin_name = _to_builtin_evaluator_name(evaluator.name)
            criterion: Dict[str, Any] = {
                "type": "azure_ai_evaluator",
                "name": evaluator.name,
                "evaluator_name": f"builtin.{builtin_name}",
                "data_mapping": _cloud_evaluator_data_mapping(
                    builtin_name,
                    input_field,
                    expected_field,
                    context_field=dataset_config.format.context_field,
                ),
            }
            if _cloud_evaluator_needs_model(builtin_name):
                if not settings.model:
                    raise ValueError(
                        f"Evaluator '{evaluator.name}' requires a model deployment name. "
                        "Set 'backend.model' in run.yaml or AZURE_AI_MODEL_DEPLOYMENT_NAME."
                    )
                criterion["initialization_parameters"] = {
                    "deployment_name": settings.model,
                }
            elif builtin_name in _NLP_DEFAULT_INIT_PARAMS:
                criterion["initialization_parameters"] = dict(
                    _NLP_DEFAULT_INIT_PARAMS[builtin_name]
                )
            testing_criteria.append(criterion)

        # --- Acquire token for Foundry Project Evals API --------------------
        try:
            evals_token = _acquire_token("https://ai.azure.com/.default")
        except Exception as exc:
            raise RuntimeError(_CREDENTIAL_HELP_MESSAGE) from exc

        evals_base_url = settings.project_endpoint.rstrip("/")
        evals_headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {evals_token}",
        }

        def _evals_post(path: str, body: Dict[str, Any]) -> Dict[str, Any]:
            url = (
                f"{evals_base_url}/openai/evals{path}?api-version={_EVALS_API_VERSION}"
            )
            return self._request_json(
                method="POST",
                url=url,
                headers=evals_headers,
                timeout_seconds=60,
                body=body,
            )

        def _evals_get(path: str, extra_params: str = "") -> Dict[str, Any]:
            params = f"api-version={_EVALS_API_VERSION}"
            if extra_params:
                params = f"{params}&{extra_params}"
            url = f"{evals_base_url}/openai/evals{path}?{params}"
            return self._request_json(
                method="GET",
                url=url,
                headers=evals_headers,
                timeout_seconds=60,
            )

        # --- Data schema ----------------------------------------------------
        item_schema: Dict[str, Any] = {
            "type": "object",
            "properties": {
                input_field: {"type": "string"},
                expected_field: {"type": "string"},
            },
            "required": [input_field, expected_field],
        }

        eval_name = f"agentops-eval-{uuid.uuid4().hex[:8]}"
        eval_object = _evals_post(
            "",
            {
                "name": eval_name,
                "data_source_config": {
                    "type": "custom",
                    "item_schema": item_schema,
                    "include_sample_schema": True,
                },
                "testing_criteria": testing_criteria,
            },
        )
        eval_id = eval_object["id"]
        logger.info("Cloud evaluation created: %s", eval_id)

        # --- Target + input messages ----------------------------------------
        input_messages: Dict[str, Any] = {
            "type": "template",
            "template": [
                {
                    "type": "message",
                    "role": "user",
                    "content": {
                        "type": "input_text",
                        "text": "{{item." + input_field + "}}",
                    },
                }
            ],
        }

        run_name = f"agentops-run-{uuid.uuid4().hex[:8]}"

        if settings.target == "model":
            # Model-direct: use completions data source (no agent)
            eval_run = _evals_post(
                f"/{eval_id}/runs",
                {
                    "name": run_name,
                    "data_source": {
                        "type": "completions",
                        "source": {
                            "type": "file_content",
                            "content": [{"item": row} for row in rows],
                        },
                        "input_messages": input_messages,
                        "model": settings.model,
                    },
                },
            )
        else:
            # Agent target
            agent_name, agent_version = _parse_agent_name_version(settings.agent_id)
            target: Dict[str, Any] = {
                "type": "azure_ai_agent",
                "name": agent_name,
            }
            if agent_version:
                target["version"] = agent_version

            eval_run = _evals_post(
                f"/{eval_id}/runs",
                {
                    "name": run_name,
                    "data_source": {
                        "type": "azure_ai_target_completions",
                        "source": {
                            "type": "file_content",
                            "content": [{"item": row} for row in rows],
                        },
                        "input_messages": input_messages,
                        "target": target,
                    },
                },
            )

        run_id = eval_run["id"]
        logger.info(
            "Cloud evaluation run started: %s  (polling every %.0fs, timeout %.0fs)",
            run_id,
            settings.poll_interval_seconds,
            settings.poll_interval_seconds * settings.max_poll_attempts,
        )

        # --- Poll until completion ------------------------------------------
        terminal_success = {"completed", "succeeded"}
        terminal_failure = {"failed", "cancelled", "canceled", "expired", "error"}
        poll_start = perf_counter()
        last_logged_status: str | None = None
        latest_run: Dict[str, Any] = eval_run

        for attempt in range(1, settings.max_poll_attempts + 1):
            latest_run = _evals_get(f"/{eval_id}/runs/{run_id}")
            run_status = str(latest_run.get("status", "unknown")).lower()

            # Only log when the status changes to avoid flooding the console.
            if run_status != last_logged_status:
                elapsed = perf_counter() - poll_start
                logger.info(
                    "Cloud eval status: %s  (%.0fs elapsed)",
                    run_status,
                    elapsed,
                )
                last_logged_status = run_status

            if run_status in terminal_success:
                break
            if run_status in terminal_failure:
                raise RuntimeError(
                    f"Foundry cloud evaluation run ended with status '{run_status}'. "
                    "Check the Foundry portal for details."
                )
            time.sleep(settings.poll_interval_seconds)
        else:
            elapsed = perf_counter() - poll_start
            raise TimeoutError(
                f"Timed out after {elapsed:.0f}s waiting for Foundry cloud evaluation"
            )

        # --- Collect output items -------------------------------------------
        output_items_resp = _evals_get(
            f"/{eval_id}/runs/{run_id}/output_items",
            extra_params="order=asc&limit=100",
        )
        output_items: List[Dict[str, Any]] = output_items_resp.get("data", [])
        if not output_items:
            raise RuntimeError(
                "Foundry cloud evaluation completed with no output items"
            )

        evaluator_aggregate_values: Dict[str, List[float]] = {
            name: [] for name in enabled_evaluator_order
        }
        # Track which local evaluators the bundle actually requests.
        enabled_local_names = frozenset(
            e.name for e in enabled_evaluators if e.source == "local"
        )

        # Approximate per-row latency from total cloud eval duration.
        eval_elapsed = perf_counter() - poll_start
        approx_latency_per_row = eval_elapsed / len(output_items)
        if {"latency_seconds", "avg_latency_seconds"} & enabled_local_names:
            logger.info(
                "Latency in cloud evaluation is estimated from total eval duration "
                "(%.1fs / %d rows ≈ %.2fs per row)",
                eval_elapsed,
                len(output_items),
                approx_latency_per_row,
            )

        row_metrics_payload: List[Dict[str, Any]] = []
        stdout_lines: List[str] = []
        stderr_lines: List[str] = []

        for index, item in enumerate(output_items, start=1):
            datasource_item = item.get("datasource_item", {}) or {}
            row_data = (
                datasource_item.get("item", datasource_item)
                if isinstance(datasource_item, dict)
                else {}
            )

            prompt = _normalize_text(row_data.get(input_field))  # noqa: F841
            expected = _normalize_text(row_data.get(expected_field))

            # Extract prediction from sample
            sample = item.get("sample", None)
            prediction = ""
            if isinstance(sample, dict):
                prediction = _normalize_text(sample.get("output_text", ""))

            row_metric_entries: List[Dict[str, float]] = []
            for result in item.get("results", []) or []:
                metric_name = result.get("name", "") if isinstance(result, dict) else ""
                metric_score = (
                    result.get("score", None) if isinstance(result, dict) else None
                )
                if isinstance(metric_name, str) and isinstance(
                    metric_score, (int, float)
                ):
                    # Normalize names like "SimilarityEvaluator-<uuid>" → "SimilarityEvaluator"
                    for eval_name in enabled_evaluator_order:
                        if metric_name == eval_name or metric_name.startswith(
                            eval_name + "-"
                        ):
                            metric_name = eval_name
                            break
                    value = float(metric_score)
                    row_metric_entries.append({"name": metric_name, "value": value})

            # Only emit local evaluator metrics if they are configured in the bundle.
            if "exact_match" in enabled_local_names:
                passed = prediction.lower() == expected.lower() if expected else False
                row_metric_entries.append(
                    {
                        "name": "exact_match",
                        "value": 1.0 if passed else 0.0,
                    }
                )
            if "latency_seconds" in enabled_local_names:
                row_metric_entries.append(
                    {
                        "name": "latency_seconds",
                        "value": approx_latency_per_row,
                    }
                )
            if "avg_latency_seconds" in enabled_local_names:
                row_metric_entries.append(
                    {
                        "name": "avg_latency_seconds",
                        "value": approx_latency_per_row,
                    }
                )

            # Update aggregate values for local evaluator metrics.
            for entry in row_metric_entries:
                agg_name = entry["name"]
                if agg_name in evaluator_aggregate_values:
                    evaluator_aggregate_values[agg_name].append(entry["value"])

            row_index = index
            datasource_item_id = item.get("datasource_item_id", None)
            if isinstance(datasource_item_id, int) and datasource_item_id >= 0:
                row_index = datasource_item_id + 1

            row_metrics_payload.append(
                {
                    "row_index": row_index,
                    "input": prompt,
                    "response": prediction,
                    "context": row_data.get("context"),
                    "metrics": row_metric_entries,
                }
            )
            stdout_lines.append(
                f"row={row_index} expected={expected!r} prediction={prediction!r}"
            )
            logger.info("Processed output item %d/%d", index, len(output_items))

        total = len(output_items)

        # --- Aggregate metrics ----------------------------------------------
        metrics_entries: List[Dict[str, float]] = []
        for name in enabled_evaluator_order:
            values = evaluator_aggregate_values.get(name, [])
            if values:
                metrics_entries.append(
                    {
                        "name": name,
                        "value": sum(values) / len(values),
                    }
                )
        metrics_entries.append({"name": "samples_evaluated", "value": float(total)})

        metrics_path.write_text(
            json.dumps(
                {"metrics": metrics_entries, "row_metrics": row_metrics_payload},
                indent=2,
            ),
            encoding="utf-8",
        )
        stdout_path.write_text("\n".join(stdout_lines), encoding="utf-8")
        stderr_path.write_text("\n".join(stderr_lines), encoding="utf-8")

        # --- Report URL (deep-link to the New Foundry Experience) -----------
        report_url = latest_run.get("report_url")

        cloud_meta_path = context.backend_output_dir / "cloud_evaluation.json"
        cloud_meta_path.write_text(
            json.dumps(
                {
                    "eval_id": eval_id,
                    "run_id": run_id,
                    "report_url": report_url,
                    "evaluation_name": eval_name,
                    "run_name": run_name,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        finished = datetime.now(timezone.utc)
        duration = perf_counter() - started_perf
        if settings.target == "model":
            command_display = (
                "foundry.cloud_evaluation "
                f"project_endpoint={settings.project_endpoint} target=model model={settings.model}"
            )
        else:
            command_display = (
                "foundry.cloud_evaluation "
                f"project_endpoint={settings.project_endpoint} target=agent agent_id={settings.agent_id} model={settings.model}"
            )

        logger.info("Cloud evaluation completed with %d output item(s)", total)
        if report_url:
            logger.info("Foundry Evaluations URL: %s", report_url)

        return BackendExecutionResult(
            backend="foundry",
            command=command_display,
            started_at=_to_utc_timestamp(started),
            finished_at=_to_utc_timestamp(finished),
            duration_seconds=duration,
            exit_code=0,
            stdout_file=stdout_path,
            stderr_file=stderr_path,
        )

    def execute(self, context: BackendRunContext) -> BackendExecutionResult:
        context.backend_output_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = context.backend_output_dir / "backend.stdout.log"
        stderr_path = context.backend_output_dir / "backend.stderr.log"
        metrics_path = context.backend_output_dir / "backend_metrics.json"

        started = datetime.now(timezone.utc)
        started_perf = perf_counter()

        stdout_lines: List[str] = []
        stderr_lines: List[str] = []
        exit_code = 0

        settings = self._read_settings(context)
        bundle_config = load_bundle_config(context.bundle_path)
        dataset_config = load_dataset_config(context.dataset_path)
        dataset_source_path = _resolve_dataset_source_path(
            context.dataset_path, dataset_config.source.path
        )
        if not dataset_source_path.exists():
            raise FileNotFoundError(f"Dataset file not found: {dataset_source_path}")

        # Cloud evaluation is the default path (New Foundry Experience).
        # Set AGENTOPS_FOUNDRY_MODE=local to use local evaluators instead.
        if _should_use_cloud_evaluation(settings.project_endpoint):
            return self._execute_cloud_evaluation(
                context=context,
                settings=settings,
                bundle_config=bundle_config,
                dataset_config=dataset_config,
                dataset_source_path=dataset_source_path,
                started=started,
                started_perf=started_perf,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                metrics_path=metrics_path,
            )

        # --- Local evaluation fallback (AGENTOPS_FOUNDRY_MODE=local) --------
        # Derive Azure OpenAI fallbacks from the Foundry project endpoint so
        # AI-assisted evaluators (SimilarityEvaluator, etc.) work without
        # requiring the user to set AZURE_OPENAI_ENDPOINT / AZURE_OPENAI_DEPLOYMENT.
        fallback_endpoint = _derive_openai_endpoint_from_project(
            settings.project_endpoint
        )
        fallback_deployment = settings.model

        enabled_evaluators = [
            evaluator for evaluator in bundle_config.evaluators if evaluator.enabled
        ]
        _validate_supported_local_evaluators(enabled_evaluators)
        enabled_evaluator_order = [evaluator.name for evaluator in enabled_evaluators]

        foundry_evaluator_runtimes = _build_foundry_evaluator_runtimes(
            enabled_evaluators,
            fallback_endpoint=fallback_endpoint,
            fallback_deployment=fallback_deployment,
        )

        rows = _load_jsonl(dataset_source_path)
        total_rows = len(rows)
        logger.info(
            "Starting local Foundry evaluation for %d dataset row(s)", total_rows
        )
        input_field = dataset_config.format.input_field
        expected_field = dataset_config.format.expected_field
        timeout_seconds = context.run_config.execution.timeout_seconds

        total = 0
        per_item_latencies: List[float] = []
        row_metrics_payload: List[Dict[str, Any]] = []
        # Track which local evaluators the bundle actually requests.
        enabled_local_names = frozenset(
            e.name for e in enabled_evaluators if e.source == "local"
        )

        evaluator_aggregate_values: Dict[str, List[float]] = {
            evaluator_name: [] for evaluator_name in enabled_evaluator_order
        }

        def _record_row_metrics(
            *,
            row_index: int,
            row_data: Dict[str, Any],
            prompt_text: str,
            expected_text: str,
            prediction_text: str,
            row_latency: float,
        ) -> None:
            nonlocal total

            prediction_normalized = _normalize_text(prediction_text)
            total += 1

            row_metric_entries: List[Dict[str, float]] = []

            for runtime in foundry_evaluator_runtimes:
                score = _run_foundry_evaluator(
                    runtime,
                    prompt=prompt_text,
                    prediction=prediction_normalized,
                    expected=expected_text,
                    row=row_data,
                )
                row_metric_entries.append({"name": runtime.name, "value": score})

            # Only emit local evaluator metrics that are configured in the bundle.
            if "exact_match" in enabled_local_names:
                passed = prediction_normalized.lower() == expected_text.lower()
                row_metric_entries.append(
                    {
                        "name": "exact_match",
                        "value": 1.0 if passed else 0.0,
                    }
                )
            if "latency_seconds" in enabled_local_names:
                row_metric_entries.append(
                    {
                        "name": "latency_seconds",
                        "value": row_latency,
                    }
                )
            if "avg_latency_seconds" in enabled_local_names:
                row_metric_entries.append(
                    {
                        "name": "avg_latency_seconds",
                        "value": row_latency,
                    }
                )

            for metric_entry in row_metric_entries:
                metric_name = metric_entry["name"]
                metric_value = metric_entry["value"]
                if metric_name in evaluator_aggregate_values:
                    evaluator_aggregate_values[metric_name].append(metric_value)

            row_metrics_payload.append(
                {
                    "row_index": row_index,
                    "input": prompt_text,
                    "response": prediction_normalized,
                    "context": row_data.get("context"),
                    "metrics": row_metric_entries,
                }
            )

            stdout_lines.append(
                f"row={row_index} expected={expected_text!r} prediction={prediction_normalized!r}"
            )

        for index, row in enumerate(rows, start=1):
            logger.info("Processing row %d/%d", index, total_rows)
            if input_field not in row:
                raise ValueError(
                    f"Dataset row {index} missing input field '{input_field}'"
                )
            if expected_field not in row:
                raise ValueError(
                    f"Dataset row {index} missing expected field '{expected_field}'"
                )

            prompt = _normalize_text(row.get(input_field))
            expected = _normalize_text(row.get(expected_field))

            row_start = perf_counter()
            try:
                if settings.target == "model":
                    prediction = self._invoke_model_direct(settings, prompt)
                else:
                    prediction = self._invoke_agent_service(
                        settings, prompt, timeout_seconds
                    )
            except urllib.error.HTTPError as exc:
                details = exc.read().decode("utf-8", errors="replace")
                if exc.code == 401 and _is_audience_mismatch(details):
                    alternate_scope = _alternate_scope(settings.token_scope)
                    try:
                        logger.info(
                            "Retrying with alternate token audience: %s",
                            alternate_scope,
                        )
                        settings = replace(
                            settings,
                            agent_token=_acquire_token(alternate_scope),
                            token_scope=alternate_scope,
                        )
                        if settings.target == "model":
                            prediction = self._invoke_model_direct(settings, prompt)
                        else:
                            prediction = self._invoke_agent_service(
                                settings, prompt, timeout_seconds
                            )
                    except Exception as retry_exc:  # noqa: BLE001
                        retry_details = str(retry_exc)
                        logger.error(
                            "Row %d/%d failed after audience retry: %s",
                            index,
                            total_rows,
                            retry_details,
                        )
                        stderr_lines.append(
                            "row="
                            f"{index} http_error={exc.code} details={details} retry_error={retry_details}"
                        )
                        exit_code = 1
                        break
                    else:
                        row_latency = perf_counter() - row_start
                        per_item_latencies.append(row_latency)

                        _record_row_metrics(
                            row_index=index,
                            row_data=row,
                            prompt_text=prompt,
                            expected_text=expected,
                            prediction_text=prediction,
                            row_latency=row_latency,
                        )
                        continue

                stderr_lines.append(
                    f"row={index} http_error={exc.code} details={details}"
                )
                logger.error("Row %d/%d HTTP error %s", index, total_rows, exc.code)
                exit_code = 1
                break
            except urllib.error.URLError as exc:
                stderr_lines.append(f"row={index} network_error={exc.reason}")
                logger.error(
                    "Row %d/%d network error: %s", index, total_rows, exc.reason
                )
                exit_code = 1
                break
            except Exception as exc:  # noqa: BLE001
                stderr_lines.append(f"row={index} error={exc}")
                logger.error("Row %d/%d failed: %s", index, total_rows, exc)
                exit_code = 1
                break

            row_latency = perf_counter() - row_start
            per_item_latencies.append(row_latency)

            _record_row_metrics(
                row_index=index,
                row_data=row,
                prompt_text=prompt,
                expected_text=expected,
                prediction_text=prediction,
                row_latency=row_latency,
            )
            logger.info("Completed row %d/%d in %.2fs", index, total_rows, row_latency)

        if total == 0 and exit_code == 0:
            raise RuntimeError("Foundry backend did not process any dataset rows")

        _avg_latency_seconds = (
            sum(per_item_latencies) / len(per_item_latencies)
            if per_item_latencies
            else 0.0
        )

        metrics_entries: List[Dict[str, float]] = []
        for evaluator_name in enabled_evaluator_order:
            values = evaluator_aggregate_values.get(evaluator_name, [])
            if values:
                metrics_entries.append(
                    {
                        "name": evaluator_name,
                        "value": sum(values) / len(values),
                    }
                )

        metrics_entries.append({"name": "samples_evaluated", "value": float(total)})

        metrics_payload = {
            "metrics": metrics_entries,
            "row_metrics": row_metrics_payload,
        }
        metrics_path.write_text(json.dumps(metrics_payload, indent=2), encoding="utf-8")
        logger.info("Local evaluation complete: processed %d row(s)", total)

        stdout_path.write_text("\n".join(stdout_lines), encoding="utf-8")
        stderr_path.write_text("\n".join(stderr_lines), encoding="utf-8")

        finished = datetime.now(timezone.utc)
        duration = perf_counter() - started_perf
        if settings.target == "model":
            command_display = (
                "foundry.model_direct "
                f"project_endpoint={settings.project_endpoint} target=model model={settings.model}"
            )
        else:
            command_display = (
                "foundry.agent_service "
                f"project_endpoint={settings.project_endpoint} target=agent agent_id={settings.agent_id} "
                f"model={settings.model} api_version={settings.api_version}"
            )

        return BackendExecutionResult(
            backend="foundry",
            command=command_display,
            started_at=_to_utc_timestamp(started),
            finished_at=_to_utc_timestamp(finished),
            duration_seconds=duration,
            exit_code=exit_code,
            stdout_file=stdout_path,
            stderr_file=stderr_path,
        )
