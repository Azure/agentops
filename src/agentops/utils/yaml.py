"""YAML load/save helpers using ruamel.yaml."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError


def load_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"YAML file not found: {path}")

    yaml = YAML(typ="safe")
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.load(handle)
    except YAMLError as exc:
        raise ValueError(f"Invalid YAML in {path}") from exc

    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"YAML root must be a mapping: {path}")
    return data


def save_yaml(path: Path, data: Dict[str, Any]) -> None:
    yaml = YAML()
    yaml.default_flow_style = False

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        yaml.dump(data, handle)
