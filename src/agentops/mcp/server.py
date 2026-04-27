"""AgentOps MCP server (stdio transport).

This module is imported lazily — the ``mcp`` extra is optional. Importing
this file triggers ``import mcp.server.fastmcp`` which fails with a clear
message if the extra is not installed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional


def _build_server() -> Any:
    """Construct and return the FastMCP server.

    Imports of ``mcp`` happen inside the function so that ``agentops --help``
    keeps working when the optional extra is not installed.
    """
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - exercised only without extra
        raise RuntimeError(
            "The MCP server requires the 'mcp' extra. "
            "Install it with: pip install agentops-toolkit[mcp]"
        ) from exc

    server = FastMCP("agentops")

    @server.tool()
    def agentops_init(directory: str = ".", force: bool = False) -> Dict[str, Any]:
        """Initialise an AgentOps workspace at ``directory``.

        Creates ``agentops.yaml`` at the project root and a tiny seed
        dataset under ``.agentops/data/smoke.jsonl``. Set ``force=True``
        to overwrite existing files.
        """
        from agentops.services.initializer import initialize_flat_workspace

        result = initialize_flat_workspace(Path(directory), force=force)
        return {
            "workspace_dir": str(result.workspace_dir),
            "created_files": [str(p) for p in result.created_files],
            "overwritten_files": [str(p) for p in result.overwritten_files],
            "skipped_files": [str(p) for p in result.skipped_files],
        }

    @server.tool()
    def agentops_eval_run(
        config_path: Optional[str] = None,
        output_dir: Optional[str] = None,
        baseline: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Run an AgentOps evaluation and return a summary.

        ``config_path`` defaults to ``agentops.yaml`` in the current
        directory. ``output_dir`` defaults to
        ``.agentops/results/latest``. ``baseline`` is an optional path to
        a previous ``results.json`` for comparison.
        """
        from agentops.core.config_loader import load_agentops_config
        from agentops.pipeline.orchestrator import (
            RunOptions,
            exit_code_from,
            run_evaluation,
        )

        config = Path(config_path) if config_path else Path("agentops.yaml")
        if not config.exists():
            return {
                "ok": False,
                "exit_code": 1,
                "error": f"config not found at {config}",
            }
        try:
            config_obj = load_agentops_config(config)
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "exit_code": 1,
                "error": f"failed to load config: {exc}",
            }

        out_dir = (
            Path(output_dir)
            if output_dir
            else config.parent / ".agentops" / "results" / "latest"
        )
        options = RunOptions(
            config_path=config.resolve(),
            output_dir=out_dir,
            baseline_path=Path(baseline).resolve() if baseline else None,
        )
        try:
            run = run_evaluation(config_obj, options=options)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "exit_code": 1, "error": f"evaluation failed: {exc}"}

        return {
            "ok": True,
            "exit_code": exit_code_from(run),
            "output_dir": str(out_dir),
            "results_json": str(out_dir / "results.json"),
            "report_md": str(out_dir / "report.md"),
            "passed": bool(run.summary.overall_passed),
            "metrics": dict(run.summary.metrics or {}),
        }

    @server.tool()
    def agentops_report_show(report_path: Optional[str] = None) -> Dict[str, Any]:
        """Return the contents of ``report.md`` for a finished run.

        Defaults to ``.agentops/results/latest/report.md``.
        """
        path = (
            Path(report_path)
            if report_path
            else Path(".agentops/results/latest/report.md")
        )
        if not path.exists():
            return {"ok": False, "error": f"report not found at {path}"}
        return {
            "ok": True,
            "path": str(path),
            "markdown": path.read_text(encoding="utf-8"),
        }

    @server.tool()
    def agentops_results_summary(results_path: Optional[str] = None) -> Dict[str, Any]:
        """Return a compact JSON summary extracted from ``results.json``.

        Defaults to ``.agentops/results/latest/results.json``.
        """
        path = (
            Path(results_path)
            if results_path
            else Path(".agentops/results/latest/results.json")
        )
        if not path.exists():
            return {"ok": False, "error": f"results not found at {path}"}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"failed to parse results.json: {exc}"}

        summary = data.get("summary") or {}
        return {
            "ok": True,
            "path": str(path),
            "version": data.get("version"),
            "target": data.get("target"),
            "rows": len(data.get("rows") or []),
            "metrics": summary.get("metrics") or {},
            "thresholds": summary.get("thresholds") or {},
            "overall_passed": summary.get("overall_passed"),
        }

    @server.tool()
    def agentops_dataset_add(
        dataset_path: str,
        rows: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Append JSONL rows to ``dataset_path``.

        Creates the parent directory if needed. Each row must be a JSON
        object — typical keys are ``input``, ``expected``, ``context``,
        and ``tool_calls`` depending on the agent type.
        """
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            return {"ok": False, "error": "rows must be a list of JSON objects"}
        path = Path(dataset_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        return {"ok": True, "path": str(path), "appended": len(rows)}

    @server.tool()
    def agentops_list_runs(workspace_dir: str = ".") -> Dict[str, Any]:
        """List historical runs under ``<workspace_dir>/.agentops/results/``."""
        results_dir = Path(workspace_dir) / ".agentops" / "results"
        if not results_dir.is_dir():
            return {"ok": True, "runs": []}
        runs: List[Dict[str, Any]] = []
        for entry in sorted(results_dir.iterdir(), key=lambda p: p.name):
            if not entry.is_dir():
                continue
            results_json = entry / "results.json"
            run_info: Dict[str, Any] = {
                "name": entry.name,
                "path": str(entry),
                "has_results": results_json.exists(),
            }
            if results_json.exists():
                try:
                    payload = json.loads(results_json.read_text(encoding="utf-8"))
                    summary = payload.get("summary") or {}
                    run_info["overall_passed"] = summary.get("overall_passed")
                    run_info["metrics"] = summary.get("metrics") or {}
                except Exception:  # noqa: BLE001
                    pass
            runs.append(run_info)
        return {"ok": True, "runs": runs}

    @server.tool()
    def agentops_workflow_init(
        directory: str = ".",
        force: bool = False,
    ) -> Dict[str, Any]:
        """Generate GitHub Actions workflows for AgentOps evaluation."""
        from agentops.services.cicd import generate_cicd_workflows

        result = generate_cicd_workflows(directory=Path(directory), force=force)
        return {
            "ok": True,
            "created_files": [str(p) for p in result.created_files],
            "overwritten_files": [str(p) for p in result.overwritten_files],
            "skipped_files": [str(p) for p in result.skipped_files],
        }

    return server


def serve_stdio() -> None:
    """Entry point for ``agentops mcp serve``.

    Builds the FastMCP server and runs it on the stdio transport. This
    function blocks until the client disconnects.
    """
    server = _build_server()
    server.run()
