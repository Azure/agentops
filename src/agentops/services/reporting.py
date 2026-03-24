"""Report orchestration service."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from agentops.core.models import RunResult
from agentops.core.reporter import generate_report_html, generate_report_markdown


@dataclass(frozen=True)
class ReportResult:
    input_results_path: Path
    output_report_path: Path


def generate_report_from_results(
    results_path: Path, output_path: Path | None = None, report_format: str = "md"
) -> ReportResult:
    resolved_results_path = results_path.resolve()
    if not resolved_results_path.exists():
        raise FileNotFoundError(f"results.json not found: {resolved_results_path}")

    payload = json.loads(resolved_results_path.read_text(encoding="utf-8"))
    result = RunResult.model_validate(payload)

    default_suffix = ".html" if report_format == "html" else ".md"
    resolved_output_path = (
        output_path.resolve()
        if output_path is not None
        else resolved_results_path.with_name(f"report{default_suffix}")
    )
    resolved_output_path.parent.mkdir(parents=True, exist_ok=True)

    primary_path = resolved_output_path
    if report_format in ("md", "all"):
        md_path = (
            resolved_output_path
            if resolved_output_path.suffix == ".md"
            else resolved_output_path.with_suffix(".md")
        )
        md_path.write_text(generate_report_markdown(result), encoding="utf-8")
        primary_path = md_path
    if report_format in ("html", "all"):
        html_path = resolved_output_path.with_suffix(".html")
        html_path.write_text(generate_report_html(result), encoding="utf-8")
        primary_path = html_path
    if report_format == "all":
        primary_path = resolved_output_path.with_suffix(".md")

    return ReportResult(
        input_results_path=resolved_results_path,
        output_report_path=primary_path,
    )
