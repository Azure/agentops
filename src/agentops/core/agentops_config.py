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
kinds - ``foundry_prompt``, ``foundry_hosted``, ``http_json``, or
``model_direct`` - based on the value shape and optional ``protocol`` field.
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

#: Where to execute the agent and evaluators.
#:
#: - ``local`` (default): AgentOps invokes the agent row-by-row and runs
#:   evaluators locally. Results are the canonical record.
#: - ``cloud``: Foundry runs the agent and evaluators server-side via the
#:   OpenAI Evals API. Use this when you want the run to appear in the
#:   New Foundry Evaluations panel as the primary record.
#: - ``azd``: Azure Developer CLI runs ``azd ai agent eval``. This is a hard
#:   dependency: if azd cannot run, AgentOps fails gracefully rather than
#:   switching engines.
#: - ``auto``: Resolve by target type only. Foundry targets use azd; target
#:   types not covered by azd use the AgentOps local engine.
ExecutionMode = Literal["local", "cloud", "azd", "auto"]

#: How cloud evaluation submits local dataset rows to Foundry.
DatasetSyncMode = Literal["auto", "inline", "foundry"]

#: Dataset shape used by the evaluator runtime or Foundry / azd recipes.
DatasetKind = Literal["auto", "single-turn", "multi-turn"]

#: Internal-only literal kept for the publisher dispatch table. Derived from
#: ``execution`` + ``publish`` via :meth:`AgentOpsConfig.publish_target`.
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
# Dataset sync configuration
# ---------------------------------------------------------------------------


class DatasetSyncConfig(BaseModel):
    """Cloud-evaluation dataset submission policy.

    AgentOps keeps the local JSONL file as the source of truth. This optional
    block tells the cloud runner whether to submit the rows inline or require a
    Foundry dataset reference once that path is validated for the Evals API.
    """

    mode: DatasetSyncMode = Field(
        "auto",
        description=(
            "Dataset submission mode for execution: cloud. 'auto' uses the "
            "safest supported mode, 'inline' forces file_content compatibility, "
            "and 'foundry' requires a versioned Foundry dataset reference."
        ),
    )
    name: Optional[str] = Field(
        None,
        description="Optional stable Foundry dataset name for synced cloud runs.",
    )
    version: str = Field(
        "content-hash",
        description=(
            "Foundry dataset version. Use 'content-hash' to derive it from the "
            "local JSONL contents, or provide an explicit version string."
        ),
    )

    model_config = ConfigDict(extra="forbid")

    @field_validator("name")
    @classmethod
    def _name_non_empty(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        value = value.strip()
        if not value:
            raise ValueError("dataset_sync.name must be non-empty when provided")
        return value

    @field_validator("version")
    @classmethod
    def _version_non_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("dataset_sync.version must be non-empty")
        return value


class RubricDimensionConfig(BaseModel):
    """One weighted dimension in a Foundry rubric evaluator.

    Rubrics are optional and additive. AgentOps records them as release
    readiness intent and uses thresholds to gate the metrics that Foundry/azd
    emits for each dimension.
    """

    name: str
    description: str
    weight: Optional[float] = None

    model_config = ConfigDict(extra="forbid")

    @field_validator("name", "description")
    @classmethod
    def _text_non_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("rubric dimension fields must be non-empty")
        return value


class RubricConfig(BaseModel):
    """Context-specific evaluator criteria for Foundry rubric scoring."""

    name: str
    description: Optional[str] = None
    dimensions: List[RubricDimensionConfig] = Field(default_factory=list)
    evaluator: Optional[str] = Field(
        None,
        description="Optional Foundry/azd evaluator name when the rubric is registered remotely.",
    )

    model_config = ConfigDict(extra="forbid")

    @field_validator("name")
    @classmethod
    def _name_non_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("rubric name must be non-empty")
        return value

    @field_validator("description", "evaluator")
    @classmethod
    def _optional_text_non_empty(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        value = value.strip()
        if not value:
            raise ValueError("rubric optional text fields must be non-empty when provided")
        return value


class TraceSamplingConfig(BaseModel):
    """Foundry intelligent trace-sampling readiness contract."""

    enabled: bool = False
    mode: Literal["manual", "foundry", "scheduled"] = "manual"
    description: Optional[str] = None

    model_config = ConfigDict(extra="forbid")

    @field_validator("description")
    @classmethod
    def _description_non_empty(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        value = value.strip()
        if not value:
            raise ValueError("observability.trace_sampling.description must be non-empty")
        return value


class ObservabilityConfig(BaseModel):
    """Foundry observability readiness metadata.

    The fields are read-only intent for Doctor, Cockpit, and release evidence.
    AgentOps does not create Foundry trace replay, sampling, or portal resources
    from this block.
    """

    tracing_enabled: bool = False
    trace_sampling: TraceSamplingConfig = Field(default_factory=TraceSamplingConfig)
    trace_replay_url: Optional[str] = None
    evaluations_url: Optional[str] = None
    datasets_url: Optional[str] = None

    model_config = ConfigDict(extra="forbid")

    @field_validator("trace_replay_url", "evaluations_url", "datasets_url")
    @classmethod
    def _url_non_empty(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        value = value.strip()
        if not value:
            raise ValueError("observability URLs must be non-empty when provided")
        if not value.startswith(("https://", "http://")):
            raise ValueError("observability URLs must start with http:// or https://")
        return value


class PromptAgentBootstrap(BaseModel):
    """Bootstrap defaults for prompt-agent CI/CD when the target Foundry
    project does not yet contain the seed agent referenced by ``agent``.

    AgentOps' Foundry prompt-agent deployment path normally looks up an
    existing seed (``name:version``) in the target project, clones its
    definition, and replaces the instructions with ``prompt_file``. That
    forces every environment (sandbox, dev, qa, prod) to have the agent
    pre-created manually.

    When ``prompt_agent_bootstrap`` is set, the deployment step instead
    bootstraps the agent in any environment whose target Foundry project
    is still empty (the seed lookup returns 404) using these values plus
    the contents of ``prompt_file``. The action recorded in the
    deployment artifact will be ``bootstrapped`` for that first run.

    This block is **only** consulted on the not-found code path. Once
    the agent exists in the target project, the reuse / next-version
    flow takes over and ``prompt_agent_bootstrap`` is ignored — changing
    ``model`` here will not migrate an existing dev agent to a new
    deployment. Treat schema changes beyond ``instructions`` as a
    deliberate operations event.

    Fields:

    ``model``
        Required. Azure OpenAI / Foundry model deployment name to use
        when creating the agent. Must exist with the same name in every
        environment that may bootstrap (sandbox, dev, qa, prod).

    ``description``
        Optional human-readable description recorded on the agent.

    ``model_parameters``
        Optional dict of model parameters (e.g. ``{"temperature": 0.2}``)
        passed through to the Foundry ``PromptAgentDefinition``.

    ``tools``
        Optional list of tool definitions (JSON-serializable dicts that
        match the Foundry tools schema) registered with the agent at
        bootstrap time.
    """

    model: str = Field(
        ...,
        description=(
            "Model deployment name. Must exist with the same name in "
            "every Foundry project that may bootstrap from this config."
        ),
    )
    description: Optional[str] = Field(
        None,
        description="Optional human-readable description for the agent.",
    )
    model_parameters: Optional[Dict[str, Any]] = Field(
        None,
        description=(
            "Optional model parameters dict (e.g. {'temperature': 0.2}) "
            "passed through to Foundry PromptAgentDefinition."
        ),
    )
    tools: Optional[List[Dict[str, Any]]] = Field(
        None,
        description=(
            "Optional tool definitions (JSON dicts matching Foundry "
            "tools schema) registered with the agent at bootstrap."
        ),
    )

    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    @field_validator("model")
    @classmethod
    def _model_non_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("prompt_agent_bootstrap.model must be non-empty")
        return value


# ---------------------------------------------------------------------------
# ASSERT runner configuration
# ---------------------------------------------------------------------------


class AssertRunConfig(BaseModel):
    """Optional configuration for orchestrating the open-source ASSERT CLI.

    When present, ``agentops assert run`` will invoke the ``assert-ai`` CLI
    against the referenced eval config and normalize the resulting artifacts
    so the evidence pack can ingest them automatically. AgentOps does not
    reimplement ASSERT; this block only declares where the ASSERT config
    lives and where ASSERT writes its outputs.

    Example::

        assert:
          config: ./assert/eval_config.yaml
          results_dir: ./artifacts/results
          suite: travel-agent-v1
          run_id: ci-run
    """

    config: Path = Field(
        ...,
        description="Path to the ASSERT eval_config.yaml that drives the run.",
    )
    results_dir: Path = Field(
        Path("artifacts") / "results",
        description=(
            "Directory under which ASSERT writes <suite>/<run>/ artifacts. "
            "Defaults to ASSERT's standard 'artifacts/results' layout."
        ),
    )
    suite: Optional[str] = Field(
        None,
        description=(
            "Optional suite id override. When omitted, AgentOps reads it "
            "from the ASSERT eval_config.yaml; if still unknown, the most "
            "recently modified suite directory is used."
        ),
    )
    run_id: Optional[str] = Field(
        None,
        description=(
            "Optional run id override. When omitted, AgentOps reads it "
            "from the ASSERT eval_config.yaml; if still unknown, the most "
            "recently modified run directory under the suite is used."
        ),
    )
    fail_on_violations: bool = Field(
        True,
        description=(
            "When true (default), 'agentops assert run' exits non-zero if "
            "ASSERT reports any policy violations. Set to false to record "
            "results without gating the pipeline."
        ),
    )

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Red Team runner configuration
# ---------------------------------------------------------------------------


class RedTeamRunConfig(BaseModel):
    """Optional configuration for orchestrating Foundry/PyRIT AI Red Teaming.

    When present, ``agentops redteam run`` will invoke Foundry's AI Red
    Teaming agent (built on the open-source PyRIT toolkit, exposed through
    ``azure.ai.evaluation.red_team.RedTeam``) against the configured target
    and write a normalized result the evidence pack can ingest automatically.
    AgentOps does not reimplement PyRIT — this block declares the target,
    risk categories, attack strategies, and gating thresholds.

    Example::

        redteam:
          target:
            model_deployment: gpt-4o-mini
          risk_categories: [violence, hate_unfairness, self_harm, sexual]
          attack_strategies: [base64, rot13, morse]
          num_objectives: 10
          fail_on_attack_success_rate: 0.2
    """

    target: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Target descriptor passed to the Foundry Red Teaming runner. "
            "Typically one of: {'model_deployment': '<deployment>'} for an "
            "Azure OpenAI deployment, or {'agent': 'name:version'} for a "
            "Foundry prompt agent, or {'endpoint': 'https://...'} for an "
            "HTTP/JSON agent. When omitted, AgentOps derives a target from "
            "the top-level 'agent' value."
        ),
    )
    risk_categories: List[str] = Field(
        default_factory=lambda: ["violence", "hate_unfairness", "self_harm", "sexual"],
        description=(
            "PyRIT risk categories to probe. Defaults to the four standard "
            "Azure AI Content Safety categories."
        ),
    )
    attack_strategies: List[str] = Field(
        default_factory=lambda: ["base64", "rot13", "morse"],
        description=(
            "PyRIT attack strategies to apply. See "
            "https://learn.microsoft.com/azure/ai-foundry/concepts/ai-red-teaming-agent "
            "for the supported strategy set."
        ),
    )
    num_objectives: int = Field(
        10,
        ge=1,
        description=(
            "Number of attack objectives to generate per risk category. "
            "Higher values increase coverage and cost."
        ),
    )
    output_path: Path = Field(
        Path(".agentops") / "redteam" / "latest.json",
        description=(
            "Where AgentOps writes the normalized red-team summary. The "
            "evidence pack auto-discovers this path via 'redteam_path'."
        ),
    )
    fail_on_attack_success_rate: Optional[float] = Field(
        0.2,
        ge=0.0,
        le=1.0,
        description=(
            "When set, 'agentops redteam run' exits non-zero if the overall "
            "attack success rate (successful attacks / total attempts) "
            "exceeds this threshold. Set to null to record results without "
            "gating the pipeline. Defaults to 0.2 (20%)."
        ),
    )

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Top-level config
# ---------------------------------------------------------------------------


_LEGACY_TOP_LEVEL_KEYS = {
    "target",
    "bundle",
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

        * ``"<name>:<version>"`` - a Foundry prompt agent (e.g. ``"my-rag:3"``).
        * ``"https://..."`` - a Foundry hosted endpoint or any HTTP/JSON agent.
        * ``"model:<deployment>"`` - a Foundry model deployment (raw model).

        See :func:`classify_agent` for the full resolution table.

    ``dataset``
        Relative path to a JSONL file with one evaluation row per line. Rows
        must contain at least ``input`` and ``expected``; optional fields
        ``context``, ``tool_calls``, and ``tool_definitions`` drive evaluator
        auto-selection.

    ``prompt_file``
        Optional source-controlled instructions file for Foundry prompt-agent
        CI/CD. Deployment workflows create a candidate Foundry agent version
        from this file, evaluate that exact version, then mark it as deployed
        only when the gate passes.

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

    ``dataset_sync``
        Optional cloud-evaluation dataset submission policy. The local JSONL
        remains the source of truth; this block controls whether cloud evals
        use inline compatibility or require a Foundry dataset reference.

    ``eval_recipe``
        Optional path to an azd ``eval.yaml`` recipe. When omitted, azd-backed
        runs discover a single recipe at the workspace root or under
        ``src/<agent>/``.

    ``assert_path`` / ``acs_path`` / ``redteam_path``
        Optional governance artifact paths. These are read-only inputs for
        Doctor and release evidence; AgentOps validates and references them but
        does not execute ASSERT, apply ACS controls, or run red-team campaigns.

    ``dataset_kind`` / ``rubrics`` / ``observability``
        Optional Foundry observability metadata. These fields keep existing
        single-turn evals working while letting Doctor, Cockpit, CI evidence, and
        azd/Foundry recipes reason about multi-turn coverage, rubric gates, trace
        sampling, and trace replay links.
    """

    version: int = Field(..., description="Schema version. Must be 1.")
    agent: str = Field(..., description="Target identifier (name:version, URL, or model:deployment)")
    dataset: Path = Field(..., description="Path to a JSONL dataset file")
    dataset_kind: DatasetKind = Field(
        "auto",
        description=(
            "Dataset shape. 'auto' preserves current behavior, 'single-turn' "
            "requires input/expected rows, and 'multi-turn' documents that rows "
            "represent conversations or message histories."
        ),
    )
    prompt_file: Optional[Path] = Field(
        None,
        description=(
            "Optional source-controlled prompt/instructions file used by "
            "prompt-agent CI/CD deployment workflows."
        ),
    )
    eval_recipe: Optional[Path] = Field(
        None,
        description=(
            "Optional path to an azd eval.yaml recipe used by execution: azd. "
            "When omitted, AgentOps discovers a single recipe in the workspace."
        ),
    )
    assert_path: Optional[Path | List[Path]] = Field(
        None,
        description="Optional ASSERT policy/results file or directory for governance evidence.",
    )
    acs_path: Optional[Path | List[Path]] = Field(
        None,
        description="Optional Agent Control Specification contract file or directory.",
    )
    redteam_path: Optional[Path | List[Path]] = Field(
        None,
        description="Optional red-team plan/results artifact path for evidence-only readiness.",
    )
    assert_run: Optional[AssertRunConfig] = Field(
        None,
        alias="assert",
        description=(
            "Optional ASSERT runner configuration. When set, 'agentops assert "
            "run' invokes the assert-ai CLI and writes normalized results that "
            "the evidence pack ingests via assert_path automatically."
        ),
    )
    redteam_run: Optional[RedTeamRunConfig] = Field(
        None,
        alias="redteam",
        description=(
            "Optional Red Team runner configuration. When set, 'agentops "
            "redteam run' invokes the Foundry/PyRIT AI Red Teaming agent and "
            "writes normalized results that the evidence pack ingests via "
            "redteam_path automatically."
        ),
    )

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
    rubrics: List[RubricConfig] = Field(
        default_factory=list,
        description="Optional context-specific rubric evaluator definitions.",
    )

    publish: bool = Field(
        False,
        description=(
            "Whether to publish results to the Foundry Evaluations panel.\n"
            "- false (default for execution: local): only local artifacts.\n"
            "- true (forced for execution: cloud): publish to Foundry.\n"
            "\n"
            "Destination is derived from 'execution':\n"
            "  execution: local + publish: true  → Classic Foundry (upload metrics)\n"
            "  execution: cloud + publish: true  → New Foundry (server-side run)\n"
            "\n"
            "execution: cloud always publishes (Foundry hosts the run by "
            "definition); setting publish: false with execution: cloud is "
            "rejected as a contradiction."
        ),
    )
    execution: ExecutionMode = Field(
        "local",
        description=(
            "Where to execute the agent and evaluators.\n"
            "- local (default): AgentOps invokes the agent row-by-row locally.\n"
            "- cloud: Foundry runs the agent and evaluators server-side, and "
            "the run is implicitly published to the New Foundry Evaluations "
            "panel (publish defaults to true).\n"
            "- azd: azd ai agent eval runs the Foundry-native evaluation. If "
            "azd cannot run, AgentOps fails gracefully and never switches "
            "engines implicitly.\n"
            "- auto: resolve by target type only; Foundry targets use azd, "
            "HTTP/model/custom targets use AgentOps local."
        ),
    )
    project_endpoint: Optional[str] = Field(
        None,
        description=(
            "Optional Foundry project endpoint URL used for Foundry target "
            "invocation and publishing. When omitted, AgentOps reads "
            "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT."
        ),
    )
    dataset_sync: DatasetSyncConfig = Field(
        default_factory=DatasetSyncConfig,
        description="Cloud evaluation dataset submission policy.",
    )
    observability: ObservabilityConfig = Field(
        default_factory=ObservabilityConfig,
        description="Foundry observability readiness metadata.",
    )
    prompt_agent_bootstrap: Optional[PromptAgentBootstrap] = Field(
        None,
        description=(
            "Optional bootstrap defaults used when the prompt-agent "
            "deployment target is empty (seed lookup returns 404). "
            "Lets CI/CD auto-create the agent in dev/qa/prod from "
            "sandbox-only authoring. See PromptAgentBootstrap docs."
        ),
    )

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @model_validator(mode="before")
    @classmethod
    def _default_publish_for_cloud(cls, data: Any) -> Any:
        """``execution: cloud`` implies ``publish: true`` when publish is
        omitted, because a cloud run is always recorded by Foundry - there
        is no way to "not publish" a server-side run.
        """
        if not isinstance(data, dict):
            return data
        if data.get("execution") == "cloud" and "publish" not in data:
            data["publish"] = True
        return data

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
    def _validate_publish_compat(self) -> "AgentOpsConfig":
        """``execution: cloud`` + ``publish: false`` is a contradiction.

        A cloud run is always recorded by Foundry - the eval definition,
        run, and artifacts live on the Foundry side as a side effect of
        executing there. ``publish: false`` cannot prevent that.
        """
        if self.execution == "cloud" and not self.publish:
            raise ValueError(
                "execution: cloud always publishes to the New Foundry "
                "Evaluations panel (Foundry hosts the run). Remove "
                "'publish: false' or set 'execution: local' if you want "
                "to keep results local-only."
            )
        return self

    @model_validator(mode="after")
    def _validate_protocol_compat(self) -> "AgentOpsConfig":
        kind = classify_agent(self.agent, self.protocol).kind
        if self.execution == "azd" and kind not in {"foundry_prompt", "foundry_hosted"}:
            raise ValueError(
                "execution: azd supports Foundry prompt or hosted agents only. "
                "Use execution: local for HTTP/JSON, model:, or custom REST targets."
            )
        if self.execution == "auto" and kind == "model_direct":
            # ``auto`` remains valid for model targets, but it resolves to the
            # AgentOps local engine because azd does not evaluate model: targets.
            pass
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

    def publish_target(self) -> Optional[PublishTarget]:
        """Return the internal publisher dispatch key, or ``None`` if disabled.

        Derived from ``execution`` + ``publish``:

        - ``publish: false`` → ``None`` (no publishing)
        - ``publish: true``, ``execution: local``  → ``"foundry"`` (Classic)
        - ``publish: true``, ``execution: cloud``  → ``"foundry_cloud"`` (New)
        """
        if not self.publish:
            return None
        if self.execution == "cloud":
            return "foundry_cloud"
        return "foundry"


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

    Heuristic - Foundry URLs include the segment ``/agents/`` and the host
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
