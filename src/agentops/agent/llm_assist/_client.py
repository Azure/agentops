"""Shared judge-model client and disk cache for LLM-assisted checks.

The client is intentionally minimal:

* Lazy-imports ``azure.ai.projects`` and ``openai`` so the base CLI does
  not require Azure SDKs.
* One :func:`LLMJudge.call` per rule. The caller supplies a Pydantic
  schema; we ask the judge for a JSON response and validate against it.
* A disk cache at ``.agentops/cache/llm/<hash>.json`` keeps repeated
  Doctor runs at zero token cost while inputs are unchanged.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Type, TypeVar

from pydantic import BaseModel, ValidationError

from agentops.agent.config import LLMAssistCheckConfig

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


@dataclass
class JudgementMeta:
    """Bookkeeping returned alongside a judge model's verdict."""

    cache_hit: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    model_deployment: Optional[str] = None


class LLMJudge:
    """Thin wrapper around the Foundry project's OpenAI client."""

    def __init__(
        self,
        config: LLMAssistCheckConfig,
        workspace: Path,
    ) -> None:
        self.config = config
        self.workspace = workspace
        self._client: Any = None
        self._deployment: Optional[str] = None

    # ------------------------------------------------------------------
    # Resolution helpers
    # ------------------------------------------------------------------

    def resolve_deployment(self) -> Optional[str]:
        """Find a deployment to use as the judge model.

        Order of resolution:

        1. Explicit ``deployment_name`` in ``LLMAssistCheckConfig``.
        2. ``deployment_name_env`` env var (default
           ``AZURE_AI_MODEL_DEPLOYMENT_NAME``).
        3. **Auto-discovery**: list deployments on the Foundry project
           and pick a chat-capable one, preferring smaller / cheaper
           models so judge calls do not blow up the project's token
           bill.

        Returns ``None`` only when none of the three paths yields a
        deployment.
        """
        if self._deployment is not None:
            return self._deployment

        deployment = self.config.deployment_name
        if not deployment and self.config.deployment_name_env:
            deployment = os.environ.get(self.config.deployment_name_env)

        if not deployment:
            deployment = self._auto_discover_deployment()

        self._deployment = deployment or None
        return self._deployment

    # Preference ranking for auto-discovery. Cheaper / smaller chat
    # models come first so the Doctor stays light on quota by default.
    _DEPLOYMENT_PREFERENCE = (
        "gpt-5.4-mini",
        "gpt-5-mini",
        "gpt-4o-mini",
        "gpt-4.1-mini",
        "gpt-5.4",
        "gpt-5",
        "gpt-4o",
        "gpt-4.1",
    )

    def _auto_discover_deployment(self) -> Optional[str]:
        """List Foundry project deployments and pick a judge-suitable one."""
        endpoint = self._resolve_endpoint()
        if not endpoint:
            return None
        try:
            from azure.ai.projects import AIProjectClient  # type: ignore
            from azure.identity import DefaultAzureCredential  # type: ignore
        except ImportError:  # pragma: no cover
            return None

        try:
            project_client = AIProjectClient(
                endpoint=endpoint,
                credential=DefaultAzureCredential(exclude_developer_cli_credential=True, process_timeout=30),
            )
            accessor = getattr(project_client, "deployments", None) or getattr(
                project_client, "models", None
            )
            if accessor is None:
                return None
            list_fn = getattr(accessor, "list", None)
            if list_fn is None:
                return None
            names = []
            for raw in list_fn():
                name = (
                    getattr(raw, "name", None)
                    or getattr(raw, "deployment_name", None)
                    or getattr(raw, "id", None)
                )
                if name:
                    names.append(str(name))
        except Exception as exc:  # pragma: no cover
            log.info("llm_assist: deployment auto-discovery failed: %s", exc)
            return None

        if not names:
            return None

        # 1. Prefer known good models from the ranked list.
        for preferred in self._DEPLOYMENT_PREFERENCE:
            for name in names:
                if name.lower() == preferred or preferred in name.lower():
                    log.info(
                        "llm_assist: auto-selected deployment %s (preferred)",
                        name,
                    )
                    return name

        # 2. Otherwise prefer anything with "mini" in the name (smaller
        # = cheaper judge calls).
        for name in names:
            if "mini" in name.lower():
                log.info(
                    "llm_assist: auto-selected deployment %s (mini fallback)",
                    name,
                )
                return name

        # 3. Last resort - first non-embedding deployment.
        for name in names:
            if "embedding" not in name.lower():
                log.info(
                    "llm_assist: auto-selected deployment %s (first non-embedding)",
                    name,
                )
                return name

        return None

    def _resolve_endpoint(self) -> Optional[str]:
        if self.config.project_endpoint:
            return self.config.project_endpoint
        if self.config.project_endpoint_env:
            return os.environ.get(self.config.project_endpoint_env)
        return None

    # ------------------------------------------------------------------
    # Cache helpers
    # ------------------------------------------------------------------

    def _cache_path(self, inputs_hash: str) -> Path:
        return (
            self.workspace
            / ".agentops"
            / "cache"
            / "llm"
            / f"{inputs_hash}.json"
        )

    def _read_cache(self, inputs_hash: str) -> Optional[Dict[str, Any]]:
        path = self._cache_path(inputs_hash)
        if not path.is_file():
            return None
        try:
            stat = path.stat()
        except OSError:
            return None
        age_days = (time.time() - stat.st_mtime) / 86400.0
        if (
            self.config.cache_ttl_days > 0
            and age_days > self.config.cache_ttl_days
        ):
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _write_cache(self, inputs_hash: str, payload: Dict[str, Any]) -> None:
        path = self._cache_path(inputs_hash)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(payload, indent=2, default=str),
                encoding="utf-8",
            )
        except OSError as exc:
            log.warning("LLM cache write failed at %s: %s", path, exc)

    # ------------------------------------------------------------------
    # Client init
    # ------------------------------------------------------------------

    def _get_client(self) -> Optional[Any]:
        if self._client is not None:
            return self._client
        endpoint = self._resolve_endpoint()
        if not endpoint:
            log.info("llm_assist: project endpoint not configured")
            return None
        try:
            from azure.ai.projects import AIProjectClient  # type: ignore
            from azure.identity import DefaultAzureCredential  # type: ignore
        except ImportError as exc:  # pragma: no cover
            log.info("llm_assist: azure-ai-projects unavailable: %s", exc)
            return None

        try:
            project_client = AIProjectClient(
                endpoint=endpoint,
                credential=DefaultAzureCredential(exclude_developer_cli_credential=True, process_timeout=30),
            )
            # Foundry exposes get_openai_client without an api_version arg;
            # never pass one (the SDK picks the right version).
            get_openai_client = getattr(project_client, "get_openai_client", None)
            if get_openai_client is None:
                inference = getattr(project_client, "inference", None)
                get_openai_client = getattr(inference, "get_openai_client", None)
            if get_openai_client is None:
                log.info("llm_assist: AIProjectClient has no OpenAI client helper")
                return None
            self._client = get_openai_client()
        except Exception as exc:  # pragma: no cover
            log.warning("llm_assist: openai client init failed: %s", exc)
            return None
        return self._client

    # ------------------------------------------------------------------
    # Public call
    # ------------------------------------------------------------------

    def call(
        self,
        *,
        system: str,
        user: str,
        schema: Type[T],
        inputs_hash: str,
    ) -> Optional[tuple[T, JudgementMeta]]:
        """Run the judge model and return a parsed ``schema`` instance.

        Returns ``None`` when the call cannot be made (missing endpoint,
        SDK not installed, judge raised) or when validation fails. The
        Doctor stays silent in those cases - LLM checks are advisory.
        """
        cached = self._read_cache(inputs_hash)
        if cached is not None:
            try:
                verdict = schema.model_validate(cached["verdict"])
                meta = JudgementMeta(
                    cache_hit=True,
                    model_deployment=cached.get("model_deployment"),
                )
                return verdict, meta
            except (ValidationError, KeyError, TypeError):
                log.info(
                    "llm_assist: cached entry %s invalid; ignoring", inputs_hash
                )

        client = self._get_client()
        deployment = self.resolve_deployment()
        if client is None or not deployment:
            return None

        try:
            response = client.chat.completions.create(
                model=deployment,
                temperature=0.0,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
        except Exception as exc:  # pragma: no cover
            log.warning("llm_assist: judge call failed: %s", exc)
            return None

        raw = ""
        usage = None
        try:
            raw = response.choices[0].message.content or ""
            usage = getattr(response, "usage", None)
        except (AttributeError, IndexError) as exc:  # pragma: no cover
            log.warning("llm_assist: judge response malformed: %s", exc)
            return None

        try:
            payload = json.loads(raw)
            verdict = schema.model_validate(payload)
        except (json.JSONDecodeError, ValidationError) as exc:
            log.warning("llm_assist: judge JSON invalid: %s", exc)
            return None

        meta = JudgementMeta(
            cache_hit=False,
            input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0)
            if usage
            else 0,
            output_tokens=int(getattr(usage, "completion_tokens", 0) or 0)
            if usage
            else 0,
            model_deployment=deployment,
        )

        self._write_cache(
            inputs_hash,
            {
                "verdict": verdict.model_dump(),
                "model_deployment": deployment,
                "input_tokens": meta.input_tokens,
                "output_tokens": meta.output_tokens,
            },
        )
        return verdict, meta
