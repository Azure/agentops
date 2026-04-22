"""Pre-flight checks for evaluation runs.

Validates the environment and configuration *before* backend execution so that
common issues (missing SDKs, env vars, unreachable endpoints, credential
failures) surface fast with actionable error messages rather than deep within
the evaluation pipeline.
"""

from __future__ import annotations

import importlib
import logging
import os
from dataclasses import dataclass, field
from urllib import error as urllib_error
from urllib import request as urllib_request

from agentops.core.models import BundleConfig, RunConfig

logger = logging.getLogger(__name__)


# Evaluator class names that need AZURE_OPENAI_ENDPOINT + AZURE_OPENAI_DEPLOYMENT.
# Sourced from _AI_ASSISTED_EVALUATORS in eval_engine.py.
_AI_ASSISTED_EVALUATOR_CLASSES = frozenset(
    {
        "GroundednessEvaluator",
        "RelevanceEvaluator",
        "CoherenceEvaluator",
        "FluencyEvaluator",
        "SimilarityEvaluator",
        "RetrievalEvaluator",
        "ResponseCompletenessEvaluator",
        "QAEvaluator",
        "IntentResolutionEvaluator",
        "TaskAdherenceEvaluator",
        "ToolCallAccuracyEvaluator",
        "TaskCompletionEvaluator",
        "TaskNavigationEfficiencyEvaluator",
        "ToolSelectionEvaluator",
        "ToolInputAccuracyEvaluator",
        "ToolOutputUtilizationEvaluator",
        "ToolCallSuccessEvaluator",
    }
)

# Safety evaluators that need AZURE_AI_FOUNDRY_PROJECT_ENDPOINT.
_SAFETY_EVALUATOR_CLASSES = frozenset(
    {
        "ViolenceEvaluator",
        "SexualEvaluator",
        "SelfHarmEvaluator",
        "HateUnfairnessEvaluator",
        "ContentSafetyEvaluator",
        "ProtectedMaterialEvaluator",
        "CodeVulnerabilityEvaluator",
        "UngroundedAttributesEvaluator",
        "IndirectAttackEvaluator",
        "GroundednessProEvaluator",
    }
)

# Local-only evaluators that don't need Azure at all.
_LOCAL_ONLY_EVALUATORS = frozenset(
    {
        "exact_match",
        "latency_seconds",
        "avg_latency_seconds",
    }
)


@dataclass
class PreflightReport:
    """Result of running pre-flight checks."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def format(self) -> str:
        lines: list[str] = []
        if self.errors:
            lines.append("Pre-flight checks failed:")
            for i, err in enumerate(self.errors, 1):
                lines.append(f"  {i}. {err}")
        if self.warnings:
            lines.append("Pre-flight warnings:")
            for warn in self.warnings:
                lines.append(f"  - {warn}")
        return "\n".join(lines)


def _needs_ai_assisted_evaluator(bundle: BundleConfig) -> bool:
    for ev in bundle.evaluators:
        if not ev.enabled:
            continue
        class_name = ev.config.get("init", {}).get("class_name") or ev.name
        if class_name in _AI_ASSISTED_EVALUATOR_CLASSES:
            return True
    return False


def _needs_safety_evaluator(bundle: BundleConfig) -> bool:
    for ev in bundle.evaluators:
        if not ev.enabled:
            continue
        class_name = ev.config.get("init", {}).get("class_name") or ev.name
        if class_name in _SAFETY_EVALUATOR_CLASSES:
            return True
    return False


def _needs_azure_sdk(bundle: BundleConfig) -> bool:
    for ev in bundle.evaluators:
        if not ev.enabled:
            continue
        if ev.source == "foundry":
            return True
        if ev.name not in _LOCAL_ONLY_EVALUATORS:
            return True
    return False


def _check_sdk_imports(report: PreflightReport, bundle: BundleConfig) -> None:
    if not _needs_azure_sdk(bundle):
        return

    try:
        importlib.import_module("azure.identity")
    except ImportError:
        report.errors.append(
            "Missing dependency 'azure-identity'. "
            "Install with: pip install azure-identity"
        )

    try:
        importlib.import_module("azure.ai.evaluation")
    except ImportError:
        report.errors.append(
            "Missing dependency 'azure-ai-evaluation'. "
            "Install with: pip install azure-ai-evaluation"
        )


def _check_env_vars(
    report: PreflightReport,
    bundle: BundleConfig,
    run_config: RunConfig,
) -> None:
    if _needs_ai_assisted_evaluator(bundle):
        # AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_DEPLOYMENT can fall back to
        # values resolved from the target endpoint, so only warn if both env
        # var and fallback are missing.
        fallback_endpoint = None
        fallback_deployment = None
        endpoint = run_config.target.endpoint
        if endpoint is not None:
            fallback_deployment = endpoint.model
            # Endpoint URL isn't directly usable as AZURE_OPENAI_ENDPOINT, so
            # only the deployment gets a fallback.

        missing: list[str] = []
        if not os.getenv("AZURE_OPENAI_ENDPOINT") and not fallback_endpoint:
            missing.append("AZURE_OPENAI_ENDPOINT")
        if not os.getenv("AZURE_OPENAI_DEPLOYMENT") and not fallback_deployment:
            missing.append("AZURE_OPENAI_DEPLOYMENT")

        if missing:
            report.errors.append(
                f"Missing required environment variable(s) for AI-assisted evaluators: "
                f"{', '.join(missing)}. "
                "Set them to your Azure OpenAI endpoint and model deployment name."
            )

    if _needs_safety_evaluator(bundle):
        if not os.getenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT"):
            report.errors.append(
                "Missing required environment variable 'AZURE_AI_FOUNDRY_PROJECT_ENDPOINT' "
                "for safety evaluators. Set it to your Foundry project endpoint URL."
            )


def _check_credentials(report: PreflightReport, bundle: BundleConfig) -> None:
    if not _needs_azure_sdk(bundle):
        return

    # Skip if SDK imports already failed — no point trying to authenticate.
    if any("azure-identity" in e for e in report.errors):
        return

    try:
        from azure.identity import DefaultAzureCredential
    except ImportError:
        return  # already reported

    try:
        credential = DefaultAzureCredential(
            exclude_developer_cli_credential=True,
            process_timeout=30,
        )
        # Warm up the token cache. This also catches credential failures
        # early, before any evaluator tries to authenticate.
        credential.get_token("https://cognitiveservices.azure.com/.default")
    except Exception as exc:  # noqa: BLE001 — surface any credential error
        report.errors.append(
            f"Azure credential check failed: {exc}. "
            "Run 'az login' or configure AZURE_CLIENT_ID/AZURE_TENANT_ID/"
            "AZURE_CLIENT_SECRET for service-principal auth. "
            "See https://aka.ms/azsdk/python/identity/defaultazurecredential/troubleshoot"
        )


def _check_endpoint_reachable(
    report: PreflightReport, run_config: RunConfig
) -> None:
    if run_config.target.execution_mode != "remote":
        return

    endpoint = run_config.target.endpoint
    if endpoint is None:
        return

    url: str | None = None
    if endpoint.kind == "http":
        url = endpoint.url
        if not url and endpoint.url_env:
            url = os.getenv(endpoint.url_env)
    elif endpoint.kind == "foundry_agent":
        url = endpoint.project_endpoint
        if not url and endpoint.project_endpoint_env:
            url = os.getenv(endpoint.project_endpoint_env)

    if not url:
        return  # endpoint resolution will fail later with a clearer message

    try:
        req = urllib_request.Request(url, method="HEAD")
        # 10s is enough for a HEAD probe; longer hints at a real problem.
        urllib_request.urlopen(req, timeout=10)  # noqa: S310 — scheme validated by config
    except urllib_error.HTTPError as exc:
        # 4xx/5xx still means the endpoint is reachable; only unreachable
        # hosts or connection errors are preflight failures.
        if exc.code >= 500:
            report.warnings.append(
                f"Endpoint reachability: {url} returned HTTP {exc.code}."
            )
    except urllib_error.URLError as exc:
        report.errors.append(
            f"Endpoint unreachable: {url} ({exc.reason}). "
            "Check the URL, network connectivity, and DNS resolution."
        )
    except Exception as exc:  # noqa: BLE001
        report.warnings.append(f"Endpoint reachability check skipped: {exc}")


def run_preflight_checks(
    run_config: RunConfig, bundle_config: BundleConfig
) -> PreflightReport:
    """Run all pre-flight checks and return a collected report.

    Checks run in order but do not short-circuit — all detectable issues are
    reported at once so the user can fix everything in a single pass.
    """
    report = PreflightReport()
    _check_sdk_imports(report, bundle_config)
    _check_env_vars(report, bundle_config, run_config)
    _check_credentials(report, bundle_config)
    _check_endpoint_reachable(report, run_config)
    return report
