#!/usr/bin/env python3
"""Lexer YAML per la generazione di laboratori Kathara."""

import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    print("Errore: PyYAML non installato. Esegui: pip install pyyaml", file=sys.stderr)
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
        f"Configurazione non trovata per '{argument}'. "
        f"Passa un file YAML valido oppure metti {argument}.yml in {config_dir}"
    )


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"File YAML non trovato: {path}")

    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise ValueError(f"Il file {path} non contiene una configurazione YAML valida.")

    return data
