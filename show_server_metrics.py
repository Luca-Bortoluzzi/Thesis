#!/usr/bin/env python3
"""Summarize TCP service and Docker container metrics produced by simulate.sh."""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as source:
        return list(csv.DictReader(source))


def number(row: dict[str, str], field: str) -> float:
    try:
        return float(row.get(field, "") or 0)
    except ValueError:
        return 0.0


def percentage(value: str) -> float:
    try:
        return float(value.strip().rstrip("%"))
    except (AttributeError, ValueError):
        return 0.0


def build_server_summary(rows: list[dict[str, str]]) -> dict[str, float]:
    last = max(rows, key=lambda row: row.get("timestamp", ""))
    duration_row = max(
        rows,
        key=lambda row: (number(row, "completed_connections"), row.get("timestamp", "")),
    )
    return {
        "active_connections": number(last, "active_connections"),
        "accepted_connections": max(number(row, "accepted_connections") for row in rows),
        "rejected_connections": max(number(row, "rejected_connections") for row in rows),
        "busy_responses": max(number(row, "busy_responses") for row in rows),
        "completed_connections": max(number(row, "completed_connections") for row in rows),
        "max_simultaneous_connections": max(
            number(row, "max_simultaneous_connections") for row in rows
        ),
        "average_connection_duration_ms": number(duration_row, "average_connection_duration_ms"),
        "max_active_threads": max(number(row, "active_threads") for row in rows),
    }


def build_scenario_summary(
    server_rows: list[dict[str, str]],
    container_rows: list[dict[str, str]],
) -> dict[str, dict[str, float]]:
    server_by_scenario: dict[str, list[dict[str, str]]] = defaultdict(list)
    container_by_scenario: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in server_rows:
        server_by_scenario[row.get("scenario") or "unknown"].append(row)
    for row in container_rows:
        container_by_scenario[row.get("scenario") or "unknown"].append(row)

    summaries: dict[str, dict[str, float]] = {}
    for scenario in sorted(set(server_by_scenario) | set(container_by_scenario)):
        service_samples = server_by_scenario.get(scenario, [])
        container_samples = container_by_scenario.get(scenario, [])
        active = [number(row, "active_connections") for row in service_samples]
        periodic_active = [
            number(row, "active_connections")
            for row in service_samples
            if row.get("event") == "sample"
        ]
        threads = [number(row, "active_threads") for row in service_samples]
        cpu = [percentage(row.get("cpu_percent", "")) for row in container_samples]
        memory = [percentage(row.get("memory_percent", "")) for row in container_samples]
        pids = [number(row, "pids") for row in container_samples]
        summaries[scenario] = {
            "service_samples": float(len(service_samples)),
            "accepted_events": float(sum(row.get("event") == "accepted" for row in service_samples)),
            "rejected_events": float(sum(row.get("event") in {"rejected", "busy"} for row in service_samples)),
            "busy_events": float(sum(row.get("event") == "busy" for row in service_samples)),
            "mean_active_connections": mean(periodic_active or active) if active else 0.0,
            "max_active_connections": max(active, default=0.0),
            "max_active_threads": max(threads, default=0.0),
            "container_samples": float(len(container_samples)),
            "mean_cpu_percent": mean(cpu) if cpu else 0.0,
            "max_cpu_percent": max(cpu, default=0.0),
            "mean_memory_percent": mean(memory) if memory else 0.0,
            "max_memory_percent": max(memory, default=0.0),
            "max_pids": max(pids, default=0.0),
        }
    return summaries


def print_report(
    server_path: Path,
    server_rows: list[dict[str, str]],
    container_rows: list[dict[str, str]],
) -> None:
    summary = build_server_summary(server_rows)
    scenarios = build_scenario_summary(server_rows, container_rows)

    print(f"[INFO] Server metrics file: {server_path}\n")
    print("Server-side availability report")
    print(f"Accepted connections: {summary['accepted_connections']:.0f}")
    print(f"Rejected connections: {summary['rejected_connections']:.0f}")
    print(f"BUSY responses sent: {summary['busy_responses']:.0f}")
    print(f"Completed connections: {summary['completed_connections']:.0f}")
    print(f"Active connections at end: {summary['active_connections']:.0f}")
    print(f"Maximum simultaneous connections: {summary['max_simultaneous_connections']:.0f}")
    print(f"Average connection duration: {summary['average_connection_duration_ms']:.2f} ms")
    print(f"Maximum active threads: {summary['max_active_threads']:.0f}")

    print("\nServer load by scenario")
    print(
        f"{'Scenario':<12} {'Samples':>8} {'Accepted':>10} {'Rejected':>10} "
        f"{'BUSY':>7} {'Active avg':>11} {'Active max':>11} {'Threads':>9}"
    )
    print("-" * 84)
    for scenario, values in scenarios.items():
        if not values["service_samples"]:
            continue
        print(
            f"{scenario:<12} {values['service_samples']:>7.0f} "
            f"{values['accepted_events']:>10.0f} {values['rejected_events']:>10.0f} "
            f"{values['busy_events']:>7.0f} "
            f"{values['mean_active_connections']:>11.2f} {values['max_active_connections']:>11.0f} "
            f"{values['max_active_threads']:>9.0f}"
        )

    if any(values["container_samples"] for values in scenarios.values()):
        print("\nContainer resources by scenario")
        print(
            f"{'Scenario':<12} {'Samples':>8} {'CPU avg':>10} {'CPU max':>10} "
            f"{'Mem avg':>10} {'Mem max':>10} {'PIDs':>8}"
        )
        print("-" * 74)
        for scenario, values in scenarios.items():
            if not values["container_samples"]:
                continue
            print(
                f"{scenario:<12} {values['container_samples']:>8.0f} "
                f"{values['mean_cpu_percent']:>9.2f}% {values['max_cpu_percent']:>9.2f}% "
                f"{values['mean_memory_percent']:>9.2f}% {values['max_memory_percent']:>9.2f}% "
                f"{values['max_pids']:>8.0f}"
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Display metrics collected from the TCP server.")
    parser.add_argument("server_csv", help="CSV generated by the instrumented TCP service")
    parser.add_argument("--container", help="Optional docker stats CSV generated by simulate.sh")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    server_path = Path(args.server_csv)
    container_path = Path(args.container) if args.container else None
    if not server_path.exists():
        print(f"[ERROR] Server metrics file not found: {server_path}", file=sys.stderr)
        return 1
    server_rows = load_rows(server_path)
    if not server_rows:
        print(f"[ERROR] Server metrics file is empty: {server_path}", file=sys.stderr)
        return 1
    container_rows = load_rows(container_path) if container_path and container_path.exists() else []
    print_report(server_path, server_rows, container_rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
