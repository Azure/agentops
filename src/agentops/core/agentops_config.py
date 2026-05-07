"""Flat ``agentops.yaml`` schema for AgentOps 1.0.

This module defines the user-facing configuration shape that replaces the
layered ``run.yaml`` + ``bundle.yaml`` + ``dataset.yaml`` files of pre-1.0
AgentOps.

Design goals:

* One file. ``agentops.yaml`` is the single source of truth.
* No ``scenario`` field. The toolkit derives the target type from the
  ``agent`` value and the evaluator set from the dataset row shape (see
  :mod:`agentops.core.evaluators`).
* No bundle / dataset YAML configs. Datasets are plain JSONL files referenced
  directly by path.

The minimal valid config is three lines::

    version: 1
    agent: my-rag-agent:3
    dataset: ./qa.jsonl

The :func:`classify_agent` helper resolves ``agent`` into one of four target
kinds — ``foundry_prompt``, ``foundry_hosted``, ``http_json``, or
``model_direct`` — based on the value shape and optional ``protocol`` field.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Public type aliases
# ---------------------------------------------------------------------------

#: Wire protocol for hosted / HTTP targets.
Protocol = Literal["responses", "invocations", "http-json"]

#: How thresholds compare against measured metric values.
Criteria = Literal[">=", ">", "<=", "<", "==", "true", "false"]

#: Resolved target kind. Derived from the ``agent`` value, never set by the user.
TargetKind = Literal[
    "foundry_prompt",   # name:version
    "foundry_hosted",   # https://...foundry... endpoint
    "http_json",        # any other https URL
    "model_direct",     # model:<deployment>
]

#: Where to publish the evaluation run. ``None`` keeps results local-only.
PublishTarget = Literal["foundry", "foundry_cloud"]


# ---------------------------------------------------------------------------
# Threshold model
# ---------------------------------------------------------------------------


class Threshold(BaseModel):
    """A pass/fail rule for a single metric.

    Users typically write thresholds as a dict keyed by metric name in
    ``agentops.yaml``::

        thresholds:
          groundedness: ">=3"
          coherence: ">=3"
          avg_latency_seconds: "<=10"

    Each value is parsed by :meth:`from_expression` into a ``Threshold``.
    """

    metric: str
    criteria: Criteria
    value: Optional[float] = None

    model_config = ConfigDict(frozen=True)

    @classmethod
    def from_expression(cls, metric: str, expression: Any) -> "Threshold":
        """Parse a shorthand string like ``">=3"`` or a bool like ``true``."""
        if isinstance(expression, bool):
            return cls(metric=metric, criteria="true" if expression else "false")
        if isinstance(expression, (int, float)):
            return cls(metric=metric, criteria=">=", value=float(expression))
        if not isinstance(expression, str):
            raise ValueError(
                f"threshold for {metric!r} must be a string, number, or bool"
            )
        text = expression.strip()
        if text.lower() in {"true", "false"}:
            return cls(metric=metric, criteria=text.lower())  # type: ignore[arg-type]
        for op in (">=", "<=", "==", ">", "<"):
            if text.startswith(op):
                rest = text[len(op):].strip()
                try:
                    return cls(metric=metric, criteria=op, value=float(rest))  # type: ignore[arg-type]
                except ValueError as exc:
                    raise ValueError(
                        f"threshold for {metric!r}: cannot parse number from {text!r}"
                    ) from exc
        raise ValueError(
            f"threshold for {metric!r}: expected '>=N', '<=N', '>N', '<N', '==N', "
            f"'true', or 'false'; got {text!r}"
        )


# ---------------------------------------------------------------------------
# Optional evaluator override (escape hatch)
# ---------------------------------------------------------------------------


class EvaluatorOverride(BaseModel):
    """Advanced override entry: force a specific evaluator into the run.

    The default user flow does **not** use this. Evaluators are auto-selected
    from the target type and dataset shape. Power users who need to bypass the
    inference rules can list evaluator names here::

        evaluators:
          - GroundednessEvaluator
          - CoherenceEvaluator
    """

    name: str

    model_config = ConfigDict(frozen=True)

    @field_validator("name")
    @classmethod
    def _name_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("evaluator name must be non-empty")
        return value


# ---------------------------------------------------------------------------
# Top-level config
# ---------------------------------------------------------------------------


_LEGACY_TOP_LEVEL_KEYS = {
    "target",
    "bundle",
    "execution",
    "output",
    "scenario",
    "backend",
    "run",
}


class AgentOpsConfig(BaseModel):
    """Top-level ``agentops.yaml`` model.

    Fields:

    ``version``
        Schema version. Must be ``1`` in this release.

    ``agent``
        The thing under evaluation. One of:

        * ``"<name>:<version>"`` — a Foundry prompt agent (e.g. ``"my-rag:3"``).
        * ``"https://..."`` — a Foundry hosted endpoint or any HTTP/JSON agent.
        * ``"model:<deployment>"`` — a Foundry model deployment (raw model).

        See :func:`classify_agent` for the full resolution table.

    ``dataset``
        Relative path to a JSONL file with one evaluation row per line. Rows
        must contain at least ``input`` and ``expected``; optional fields
        ``context``, ``tool_calls``, and ``tool_definitions`` drive evaluator
        auto-selection.

    ``thresholds``
        Optional dict of metric name → criteria expression. When omitted, the
        evaluator catalog provides sensible defaults per metric.

    ``protocol``
        Optional, only relevant for URL-based ``agent`` values. Defaults to
        ``"responses"`` for Foundry hosted endpoints and ``"http-json"`` for
        any other HTTPS URL.

    ``request_field`` / ``response_field`` / ``tool_calls_field``
        ``http-json`` and ``invocations`` only. JSON keys / dot-paths used to
        marshal each dataset row into the request body and to extract the
        response. Defaults are sensible for OpenAI-compatible / ACA endpoints.

    ``headers`` / ``auth_header_env``
        Optional HTTP request configuration for ``http-json`` and
        ``invocations`` targets.

    ``evaluators``
        Optional escape hatch: explicit list of evaluator names that overrides
        the auto-selection rules. Most users should leave this unset.
    """

    version: int = Field(..., description="Schema version. Must be 1.")
    agent: str = Field(..., description="Target identifier (name:version, URL, or model:deployment)")
    dataset: Path = Field(..., description="Path to a JSONL dataset file")

    thresholds: Dict[str, Any] = Field(
        default_factory=dict,
        description="Metric name -> criteria expression (e.g. '>=3').",
    )

    protocol: Optional[Protocol] = None
    request_field: Optional[str] = None
    response_field: Optional[str] = None
    tool_calls_field: Optional[str] = None
    headers: Dict[str, str] = Field(default_factory=dict)
    auth_header_env: Optional[str] = None

    evaluators: Optional[List[EvaluatorOverride]] = None

    publish: Optional[PublishTarget] = Field(
        None,
        description=(
            "Optional opt-in publish target.\n"
            "- 'foundry' (Classic): runs locally, uploads computed metrics "
            "to the Classic Foundry Evaluations panel via OneDP.\n"
            "- 'foundry_cloud' (preview): submits the run to the New Foundry "
            "experience via the OpenAI Evals API. The agent and evaluators "
            "execute server-side; agent must be a 'name:version' Foundry "
            "agent."
        ),
    )
    project_endpoint: Optional[str] = Field(
        None,
        description=(
            "Optional Foundry project endpoint URL used by 'publish: foundry'. "
            "When omitted, AGENTOPS reads AZURE_AI_FOUNDRY_PROJECT_ENDPOINT."
        ),
    )

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _reject_legacy(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        legacy = _LEGACY_TOP_LEVEL_KEYS & set(data.keys())
        if legacy:
            raise ValueError(
                "agentops.yaml uses the new flat schema (see docs/concepts.md). "
                f"Remove legacy keys: {sorted(legacy)}. The minimal config is "
                "version + agent + dataset."
            )
        return data

    @field_validator("version")
    @classmethod
    def _check_version(cls, value: int) -> int:
        if value != 1:
            raise ValueError(
                f"agentops.yaml version must be 1 (got {value!r})"
            )
        return value

    @field_validator("agent")
    @classmethod
    def _agent_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("agent must be non-empty")
        return value.strip()

    @model_validator(mode="after")
    def _validate_protocol_compat(self) -> "AgentOpsConfig":
        kind = classify_agent(self.agent, self.protocol).kind
        if kind == "foundry_prompt" and self.protocol is not None:
            raise ValueError(
                "agent of the form 'name:version' is a Foundry prompt agent "
                "and does not accept a 'protocol' field"
            )
        if kind == "model_direct" and self.protocol is not None:
            raise ValueError(
                "agent of the form 'model:<deployment>' does not accept a "
                "'protocol' field"
            )
        if kind != "http_json" and (
            self.request_field
            or self.response_field
            or self.tool_calls_field
            or self.headers
            or self.auth_header_env
        ):
            # Foundry hosted (responses/invocations) defines its own wire
            # format. HTTP-only request/response shaping is invalid there.
            if kind == "foundry_hosted" and self.protocol == "invocations":
                # Invocations passes JSON through; users may need headers.
                pass
            else:
                raise ValueError(
                    "request_field / response_field / tool_calls_field / "
                    "headers / auth_header_env are only valid for HTTP/JSON "
                    "or Foundry hosted (invocations) targets"
                )
        return self

    def parsed_thresholds(self) -> List[Threshold]:
        """Return the threshold dict parsed into structured rules."""
        return [
            Threshold.from_expression(metric, expression)
            for metric, expression in self.thresholds.items()
        ]

    def resolved_target(self) -> "TargetResolution":
        """Return the resolved target classification."""
        return classify_agent(self.agent, self.protocol)


# ---------------------------------------------------------------------------
# Agent classifier
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TargetResolution:
    """Result of classifying the ``agent`` field."""

    kind: TargetKind
    protocol: Optional[Protocol]
    raw: str
    #: For ``foundry_prompt``: the agent name (left of the colon).
    name: Optional[str] = None
    #: For ``foundry_prompt``: the version (right of the colon).
    version: Optional[str] = None
    #: For ``foundry_hosted`` / ``http_json``: the target URL.
    url: Optional[str] = None
    #: For ``model_direct``: the deployment name.
    deployment: Optional[str] = None


def _looks_like_foundry_url(url: str) -> bool:
    """Return ``True`` when ``url`` matches a Foundry hosted endpoint pattern.

    Heuristic — Foundry URLs include the segment ``/agents/`` and the host
    ends in a Foundry-recognized domain. We err on the side of accepting more
    URLs as Foundry hosted (the user can force ``http-json`` via ``protocol``).
    """
    lowered = url.lower()
    foundry_domains = (
        ".azure.com",
        ".azureml.ms",
        ".cognitiveservices.azure.com",
        ".services.ai.azure.com",
        ".inference.ml.azure.com",
        ".azurewebsites.net",  # rare; users can override
    )
    return any(domain in lowered for domain in foundry_domains)


def classify_agent(
    agent: str,
    protocol: Optional[Protocol] = None,
) -> TargetResolution:
    """Classify the ``agent`` value into a target kind.

    Resolution table:

    +-------------------------+--------------------------+-----------------------+
    | ``agent`` value         | ``protocol``             | ``TargetKind``        |
    +=========================+==========================+=======================+
    | ``model:gpt-4o``        | n/a                      | ``model_direct``      |
    +-------------------------+--------------------------+-----------------------+
    | ``my-rag:3``            | n/a                      | ``foundry_prompt``    |
    +-------------------------+--------------------------+-----------------------+
    | ``https://...foundry``  | omitted or ``responses`` | ``foundry_hosted``    |
    | (foundry-shaped URL)    |                          | (responses)           |
    +-------------------------+--------------------------+-----------------------+
    | ``https://...foundry``  | ``invocations``          | ``foundry_hosted``    |
    |                         |                          | (invocations)         |
    +-------------------------+--------------------------+-----------------------+
    | ``https://other-host``  | omitted or ``http-json`` | ``http_json``         |
    +-------------------------+--------------------------+-----------------------+
    """
    raw = agent.strip()

    if raw.lower().startswith("model:"):
        deployment = raw.split(":", 1)[1].strip()
        if not deployment:
            raise ValueError("model: prefix requires a deployment name")
        return TargetResolution(
            kind="model_direct",
            protocol=None,
            raw=raw,
            deployment=deployment,
        )

    lowered = raw.lower()
    if lowered.startswith(("http://", "https://")):
        if _looks_like_foundry_url(raw):
            resolved_protocol: Protocol = protocol or "responses"
            if resolved_protocol not in {"responses", "invocations"}:
                raise ValueError(
                    "Foundry hosted endpoints accept only protocol "
                    "'responses' or 'invocations'"
                )
            return TargetResolution(
                kind="foundry_hosted",
                protocol=resolved_protocol,
                raw=raw,
                url=raw,
            )

        resolved_protocol = protocol or "http-json"
        if resolved_protocol != "http-json":
            raise ValueError(
                "non-Foundry URLs must use protocol 'http-json' "
                f"(got {resolved_protocol!r})"
            )
        return TargetResolution(
            kind="http_json",
            protocol="http-json",
            raw=raw,
            url=raw,
        )

    if ":" in raw:
        name, _, version = raw.partition(":")
        name = name.strip()
        version = version.strip()
        if not name or not version:
            raise ValueError(
                "Foundry prompt agent must be 'name:version' "
                f"(got {raw!r})"
            )
        return TargetResolution(
            kind="foundry_prompt",
            protocol=None,
            raw=raw,
            name=name,
            version=version,
        )

    raise ValueError(
        f"unrecognized agent value {raw!r}: expected 'name:version', "
        "'https://...', or 'model:<deployment>'"
    )
