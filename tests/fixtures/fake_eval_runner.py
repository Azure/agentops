from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Deterministic fake backend for tests.")
    parser.add_argument("--bundle", required=True, help="Path to bundle config file")
    parser.add_argument("--dataset", required=True, help="Path to dataset config file")
    parser.add_argument("--output", required=True, help="Output directory path")
    return parser.parse_args()


def build_metrics_payload(bundle_path: str, dataset_path: str) -> dict[str, Any]:
    return {
        "bundle": bundle_path,
        "dataset": dataset_path,
        "metrics": [
            {"name": "groundedness", "value": 0.84},
            {"name": "relevance", "value": 0.83},
            {"name": "coherence", "value": 0.82},
            {"name": "fluency", "value": 0.81},
        ],
        "row_metrics": [
            {
                "row_index": 1,
                "metrics": [
                    {"name": "groundedness", "value": 0.84},
                    {"name": "relevance", "value": 0.83},
                    {"name": "coherence", "value": 0.82},
                    {"name": "fluency", "value": 0.81},
                ],
            },
            {
                "row_index": 2,
                "metrics": [
                    {"name": "groundedness", "value": 0.84},
                    {"name": "relevance", "value": 0.83},
                    {"name": "coherence", "value": 0.82},
                    {"name": "fluency", "value": 0.81},
                ],
            },
        ],
    }


def main() -> int:
    args = parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    output_file = output_dir / "backend_metrics.json"
    payload = build_metrics_payload(args.bundle, args.dataset)

    output_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
