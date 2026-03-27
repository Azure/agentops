"""Fake local adapter for integration tests.

Reads a JSON row from stdin, echoes the input as the response.
This produces deterministic exact-match results for testing.
"""
from __future__ import annotations

import json
import sys


def main() -> int:
    raw = sys.stdin.read()
    row = json.loads(raw)
    # Echo input as response for deterministic exact_match scoring
    response = row.get("input", "")
    print(json.dumps({"response": response}))
    return 0


def main_callable(input_text: str, context: dict) -> dict:
    """Callable adapter entry point for integration tests.

    Echoes the input as the response, matching the subprocess adapter
    behavior for deterministic exact_match scoring.
    """
    return {"response": input_text}


if __name__ == "__main__":
    raise SystemExit(main())
