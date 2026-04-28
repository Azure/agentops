"""Render scenario-specific agentops.yaml files for the e2e workflow.

Reads target identifiers from environment variables (set by the GitHub
Actions workflow from repo Actions Variables + Bicep outputs) and writes
one agentops.yaml per scenario into ``./e2e-runs/<scenario>/``.

Scenarios:
  - foundry-prompt: AGENTOPS_E2E_FOUNDRY_PROMPT_AGENT (e.g. ``e2e-prompt:1``)
  - foundry-hosted: AGENTOPS_E2E_FOUNDRY_HOSTED_URL  (https URL)
  - http-aca:      AGENTOPS_E2E_ACA_URL              (https URL of echo app)
  - model-direct:  AGENTOPS_E2E_MODEL_DEPLOYMENT     (deployment name)

A scenario is skipped (no file written) when its env var is unset, which
lets the workflow run partial scenarios via ``workflow_dispatch.inputs``.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATASET_BASIC = ROOT / "scripts" / "e2e_data" / "basic.jsonl"
DATASET_RAG = ROOT / "scripts" / "e2e_data" / "rag.jsonl"


def _ensure_datasets() -> None:
    DATASET_BASIC.parent.mkdir(parents=True, exist_ok=True)
    if not DATASET_BASIC.exists():
        rows = [
            {"input": "What is 2+2?", "expected": "4"},
            {"input": "Capital of France?", "expected": "Paris"},
            {"input": "Color of the sky on a clear day?", "expected": "blue"},
        ]
        DATASET_BASIC.write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
        )
    if not DATASET_RAG.exists():
        rows = [
            {
                "input": "What is the capital of France?",
                "expected": "Paris",
                "context": "France is a country in Western Europe. Its capital is Paris.",
            },
            {
                "input": "What language is spoken in Brazil?",
                "expected": "Portuguese",
                "context": "Brazil is a South American country. The official language is Portuguese.",
            },
        ]
        DATASET_RAG.write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
        )


def _write(scenario: str, body: str) -> Path:
    out_dir = ROOT / "e2e-runs" / scenario
    out_dir.mkdir(parents=True, exist_ok=True)
    cfg = out_dir / "agentops.yaml"
    cfg.write_text(body, encoding="utf-8")
    return cfg


def render() -> list[str]:
    _ensure_datasets()
    written: list[str] = []
    rel_basic = DATASET_BASIC.relative_to(ROOT).as_posix()
    rel_rag = DATASET_RAG.relative_to(ROOT).as_posix()

    prompt_agent = os.environ.get("AGENTOPS_E2E_FOUNDRY_PROMPT_AGENT")
    if prompt_agent:
        _write(
            "foundry-prompt",
            f"""version: 1
agent: {prompt_agent}
dataset: ../../{rel_basic}
""",
        )
        written.append("foundry-prompt")

    hosted_url = os.environ.get("AGENTOPS_E2E_FOUNDRY_HOSTED_URL")
    if hosted_url:
        _write(
            "foundry-hosted",
            f"""version: 1
agent: {hosted_url}
dataset: ../../{rel_rag}
protocol: responses
""",
        )
        written.append("foundry-hosted")

    aca_url = os.environ.get("AGENTOPS_E2E_ACA_URL")
    if aca_url:
        _write(
            "http-aca",
            f"""version: 1
agent: {aca_url}
dataset: ../../{rel_basic}
protocol: http-json
request_field: message
response_field: body.message
""",
        )
        written.append("http-aca")

    model_deployment = os.environ.get("AGENTOPS_E2E_MODEL_DEPLOYMENT")
    if model_deployment:
        _write(
            "model-direct",
            f"""version: 1
agent: model:{model_deployment}
dataset: ../../{rel_basic}
""",
        )
        written.append("model-direct")

    return written


def main() -> int:
    written = render()
    if not written:
        print("ERROR: no scenario env vars set; nothing to render.", file=sys.stderr)
        return 1
    for s in written:
        print(f"rendered: e2e-runs/{s}/agentops.yaml")
    return 0


if __name__ == "__main__":
    sys.exit(main())
