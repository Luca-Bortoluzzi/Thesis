#!/usr/bin/env python3
"""Syntax validation for Kathara lab YAML configurations."""

from pathlib import Path
from typing import Any

from src_gen_lab.Lexer import load_yaml, resolve_config_path


def validate_config(config: dict[str, Any]) -> None:
    if "lab_name" not in config:
        raise ValueError("Missing required field: lab_name")

    if "nodes" not in config:
        raise ValueError("Missing required field: nodes")

    if not isinstance(config["nodes"], dict) or not config["nodes"]:
        raise ValueError("The nodes field must be a non-empty dictionary.")

    for node_name, node_data in config["nodes"].items():
        if not isinstance(node_data, dict):
            raise ValueError(f"Node {node_name} must be a dictionary.")

        interfaces = node_data.get("interfaces")
        if not isinstance(interfaces, list) or not interfaces:
            raise ValueError(f"Node {node_name} must have at least one interface.")

        for index, interface in enumerate(interfaces):
            if not isinstance(interface, dict):
                raise ValueError(f"Node {node_name}, interface {index}, is invalid.")

            if "network" not in interface:
                raise ValueError(
                    f"Node {node_name}, interface {index}, has no network field."
                )


def parse_yaml_configuration(argument: str, config_dir: Path) -> tuple[Path, dict[str, Any]]:
    config_path = resolve_config_path(argument, config_dir)
    config = load_yaml(config_path)
    validate_config(config)
    return config_path, config
