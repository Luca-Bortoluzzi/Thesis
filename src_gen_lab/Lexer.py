#!/usr/bin/env python3
"""YAML loader utilities for Kathara lab generation."""

import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    print("Error: PyYAML is not installed. Run: pip install pyyaml", file=sys.stderr)
    raise

YAML_EXTENSIONS = (".yml", ".yaml")


def resolve_config_path(argument: str, config_dir: Path) -> Path:
    candidate = Path(argument)
    if candidate.exists():
        return candidate

    for extension in YAML_EXTENSIONS:
        possible = config_dir / f"{argument}{extension}"
        if possible.exists():
            return possible

    raise FileNotFoundError(
        f"Configuration not found for '{argument}'. "
        f"Pass a valid YAML file or place {argument}.yml in {config_dir}"
    )


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"YAML file not found: {path}")

    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise ValueError(f"File {path} does not contain a valid YAML configuration.")

    return data
