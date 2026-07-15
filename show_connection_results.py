#!/usr/bin/env python3
"""
Reads CSV files produced by run_connection_tests.py and generates a measurement report.

The current schema separates ICMP RTT, TCP connection time, and application
response time. Historical CSV files containing ``elapsed_ms`` remain readable,
but the value is correctly identified as a legacy Docker-exec-inclusive metric.
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean, median


NETWORK_METRICS = [
    ("icmp_rtt_ms", "ICMP RTT"),
    ("tcp_connect_ms", "TCP connection"),
    ("application_response_ms", "Application response"),
    ("request_completion_ms", "TCP request completion"),
]


def find_latest_csv(lab_dir: Path) -> Path | None:
    results_dir = lab_dir / "results"
    if not results_dir.exists():
        return None
    files = sorted(results_dir.rglob("connection_results_*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def load_rows(csv_path: Path) -> list[dict[str, str]]:
    with csv_path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def safe_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def safe_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def parse_iso(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def is_current_schema(rows: list[dict[str, str]]) -> bool:
    return bool(rows and "application_response_ms" in rows[0])


def application_ok(row: dict[str, str]) -> bool:
    return (row.get("application_status") or row.get("status")) == "OK"


def contains_timeout(value: str | None) -> bool:
    return "timeout" in (value or "").lower()


def application_error(row: dict[str, str]) -> str:
    return row.get("application_error") or row.get("error") or ""


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(fraction * len(ordered)) - 1)
    return ordered[index]


def metric_values(rows: list[dict[str, str]], field: str) -> list[float]:
    values = [safe_float(row.get(field)) for row in rows]
    return [value for value in values if value is not None]


def format_ms(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.2f}"


def experiment_duration_seconds(rows: list[dict[str, str]]) -> float | None:
    elapsed = metric_values(rows, "experiment_elapsed_ms")
    if elapsed:
        return max(elapsed) / 1000

    timestamps = [parse_iso(row.get("timestamp", "")) for row in rows]
    timestamps = [value for value in timestamps if value is not None]
    if len(timestamps) >= 2:
        return (max(timestamps) - min(timestamps)).total_seconds()
    return None


def print_metric_table(metrics: list[tuple[str, object]]) -> None:
    print("Overall metrics")
    print(f"{'Metric':<42} {'Value':<18}")
    print("-" * 62)
    for name, value in metrics:
        print(f"{name:<42} {str(value):<18}")


def print_distribution_table(rows: list[dict[str, str]]) -> None:
    current = is_current_schema(rows)
    definitions = NETWORK_METRICS if current else [
        ("elapsed_ms", "Legacy end-to-end latency (*)"),
    ]

    print("\nLatency distribution (successful samples only, ms)")
    print(f"{'Metric':<32} {'N':>5} {'Mean':>10} {'Min':>10} {'P50':>10} {'P95':>10} {'Max':>10}")
    print("-" * 92)
    for field, label in definitions:
        values = metric_values(rows, field)
        if not values:
            print(f"{label:<32} {0:>5} {'N/A':>10} {'N/A':>10} {'N/A':>10} {'N/A':>10} {'N/A':>10}")
            continue
        print(
            f"{label:<32} {len(values):>5} {mean(values):>10.2f} {min(values):>10.2f} "
            f"{median(values):>10.2f} {percentile(values, 0.95):>10.2f} {max(values):>10.2f}"
        )
    if not current:
        print("(*) Includes docker exec, shell, and nc overhead; it is not a network RTT.")


def print_client_table(client_stats: dict[str, dict[str, object]]) -> None:
    print("\nPer-client summary (application metric)")
    print(
        f"{'Client':<15} {'Requests':>9} {'OK':>6} {'Fail':>6} "
        f"{'Availability':>13} {'Timeouts':>9} {'Mean ms':>10} {'Min ms':>9} {'Max ms':>9}"
    )
    print("-" * 94)
    for client in sorted(client_stats):
        s = client_stats[client]
        print(
            f"{client:<15} {s['total']:>9} {s['ok']:>6} {s['fail']:>6} "
            f"{s['availability']:>11} {s['timeouts']:>8} {s['avg_ms']:>10} "
            f"{s['min_ms']:>9} {s['max_ms']:>9}"
        )


def build_report(
    rows: list[dict[str, str]],
) -> tuple[list[tuple[str, object]], dict[str, dict[str, object]], Counter[str]]:
    total = len(rows)
    ok = sum(1 for row in rows if application_ok(row))
    fail = total - ok
    current = is_current_schema(rows)
    availability = (ok / total * 100) if total else 0.0

    duration = experiment_duration_seconds(rows)
    request_timeout = sum(
        1 for row in rows
        if contains_timeout(row.get("tcp_error")) or contains_timeout(application_error(row))
    )
    application_timeout = sum(1 for row in rows if application_error(row) == "application_timeout")
    icmp_timeout = sum(1 for row in rows if contains_timeout(row.get("icmp_error"))) if current else 0

    metrics: list[tuple[str, object]] = [
        ("Total requests", total),
        ("Completed application requests", ok),
        ("Failed application requests", fail),
        ("Application availability", f"{availability:.2f}%"),
        ("Requests with TCP/app timeout", request_timeout),
    ]
    if current:
        metrics.extend([
            ("TCP connection timeouts", sum(1 for row in rows if contains_timeout(row.get("tcp_error")))),
            ("Application response timeouts", application_timeout),
            ("ICMP timeouts", icmp_timeout),
        ])
    metrics.append(("Effective experiment duration", f"{duration:.3f} s" if duration is not None else "N/A"))

    latency_field = "application_response_ms" if current else "elapsed_ms"
    client_raw: dict[str, dict[str, object]] = defaultdict(
        lambda: {"total": 0, "ok": 0, "fail": 0, "timeouts": 0, "elapsed": []}
    )
    for row in rows:
        client = row.get("client", "N/D")
        data = client_raw[client]
        data["total"] = int(data["total"]) + 1
        if application_ok(row):
            data["ok"] = int(data["ok"]) + 1
            value = safe_float(row.get(latency_field))
            if value is not None:
                elapsed = data["elapsed"]
                assert isinstance(elapsed, list)
                elapsed.append(value)
        else:
            data["fail"] = int(data["fail"]) + 1
        if contains_timeout(row.get("tcp_error")) or contains_timeout(application_error(row)):
            data["timeouts"] = int(data["timeouts"]) + 1

    client_stats: dict[str, dict[str, object]] = {}
    for client, data in client_raw.items():
        elapsed = data["elapsed"]
        assert isinstance(elapsed, list)
        client_total = int(data["total"])
        client_ok = int(data["ok"])
        client_stats[client] = {
            "total": client_total,
            "ok": client_ok,
            "fail": data["fail"],
            "availability": f"{(client_ok / client_total * 100) if client_total else 0:.2f}%",
            "timeouts": data["timeouts"],
            "avg_ms": format_ms(mean(elapsed) if elapsed else None),
            "min_ms": format_ms(min(elapsed) if elapsed else None),
            "max_ms": format_ms(max(elapsed) if elapsed else None),
        }

    errors: Counter[str] = Counter()
    for row in rows:
        if current:
            for prefix, status_field, error_field in (
                ("ICMP", "icmp_status", "icmp_error"),
                ("TCP", "tcp_status", "tcp_error"),
            ):
                if row.get(status_field) != "OK":
                    errors[f"{prefix}: {row.get(error_field) or 'unspecified_error'}"] += 1
            if row.get("application_status") != "OK":
                errors[f"Application: {application_error(row) or 'unspecified_error'}"] += 1
        elif not application_ok(row):
            errors[row.get("error") or "unspecified_error"] += 1

    return metrics, client_stats, errors


def infer_context(rows: list[dict[str, str]]) -> dict[str, str]:
    first = rows[0] if rows else {}
    clients = sorted({row.get("client", "") for row in rows if row.get("client")})
    target = first.get("target", "N/D")
    if first.get("target_input") and first.get("target_input") != target:
        target = f"{first.get('target_input')} -> {target}"
    return {
        "scenario": first.get("scenario", "N/D"),
        "target": target,
        "port": first.get("port", "N/D"),
        "clients": ", ".join(clients) if clients else "N/D",
    }


def derive_plot_path(csv_path: Path) -> Path:
    plots_dir = csv_path.parent / "plots"
    plots_dir.mkdir(exist_ok=True)
    return plots_dir / f"{csv_path.stem}_metrics.png"


def generate_metrics_plot(rows: list[dict[str, str]], csv_path: Path, output_path: Path | None = None) -> Path:
    try:
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise RuntimeError("matplotlib is not installed. Install it with: pip install matplotlib") from exc

    if output_path is None:
        output_path = derive_plot_path(csv_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    current = is_current_schema(rows)
    plot_metrics = NETWORK_METRICS[:3] if current else [("elapsed_ms", "Latenza end-to-end legacy")]
    plot_count = len(plot_metrics) + (1 if current else 0)
    figure, axes = plt.subplots(plot_count, 1, figsize=(11, 3.5 * plot_count), squeeze=False)
    context = infer_context(rows)
    plotted = False

    for axis, (field, label) in zip(axes.flat, plot_metrics):
        by_client: dict[str, list[tuple[int, float]]] = defaultdict(list)
        for row in rows:
            index = safe_int(row.get("request_index") or row.get("attempt") or row.get("packet_number"))
            value = safe_float(row.get(field))
            if index is not None and value is not None:
                by_client[row.get("client", "N/D")].append((index, value))
        for client in sorted(by_client):
            points = sorted(by_client[client])
            axis.plot([p[0] for p in points], [p[1] for p in points], marker="o", label=client)
            plotted = True
        axis.set_title(label)
        axis.set_xlabel("Request index")
        axis.set_ylabel("Time (ms)")
        axis.grid(True, alpha=0.3)
        if by_client:
            axis.legend(ncol=min(4, len(by_client)), fontsize="small")

    if current:
        availability_axis = axes.flat[-1]
        round_totals: Counter[int] = Counter()
        round_successes: Counter[int] = Counter()
        for row in rows:
            index = safe_int(row.get("request_index"))
            if index is None:
                continue
            round_totals[index] += 1
            if application_ok(row):
                round_successes[index] += 1
        round_indexes = sorted(round_totals)
        percentages = [round_successes[index] / round_totals[index] * 100 for index in round_indexes]
        availability_axis.plot(round_indexes, percentages, color="tab:red", marker="o", linewidth=2)
        availability_axis.fill_between(round_indexes, percentages, alpha=0.18, color="tab:red")
        availability_axis.set_title("Application availability by round")
        availability_axis.set_xlabel("Request index")
        availability_axis.set_ylabel("Availability (%)")
        availability_axis.set_ylim(-5, 105)
        availability_axis.grid(True, alpha=0.3)
        for index, percentage in zip(round_indexes, percentages):
            availability_axis.annotate(
                f"{percentage:.0f}%",
                (index, percentage),
                xytext=(0, 7),
                textcoords="offset points",
                ha="center",
                fontsize="small",
            )

    if not plotted:
        plt.close(figure)
        raise RuntimeError("No timing samples are available to generate a plot.")
    figure.suptitle(f"Network and application metrics - scenario {context['scenario']}")
    figure.tight_layout()
    figure.savefig(output_path, dpi=150)
    plt.close(figure)
    return output_path


def generate_rtt_plot(rows: list[dict[str, str]], csv_path: Path, output_path: Path | None = None) -> Path:
    """Compatibility alias for callers importing the previous function."""
    return generate_metrics_plot(rows, csv_path, output_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Display a report of network and application measurements.")
    parser.add_argument("csv_file", nargs="?", help="CSV file to read. Defaults to the latest file in results/.")
    parser.add_argument("--plot", action="store_true", help="Generate ICMP RTT, TCP, application, and availability plots.")
    parser.add_argument("--plot-output", help="Output PNG path. Default: results/plots/<csv>_metrics.png")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    csv_path = Path(args.csv_file) if args.csv_file else find_latest_csv(Path.cwd())
    if csv_path is None or not csv_path.exists():
        print("[ERROR] No CSV file found. Run ./run_connection_tests.py first.", file=sys.stderr)
        return 1

    rows = load_rows(csv_path)
    if not rows:
        print(f"[ERROR] CSV file is empty: {csv_path}", file=sys.stderr)
        return 1

    context = infer_context(rows)
    metrics, client_stats, errors = build_report(rows)
    print(f"[INFO] Results file: {csv_path}\n")
    print("Network and application measurement report")
    print(f"Scenario: {context['scenario']}")
    print(f"Target: {context['target']}:{context['port']}")
    print(f"Tested clients: {context['clients']}\n")
    print_metric_table(metrics)
    print_distribution_table(rows)
    print_client_table(client_stats)

    print("\nError details")
    if not errors:
        print("no errors detected")
    else:
        for error, count in errors.most_common():
            print(f"- {error}: {count}")

    if args.plot:
        output_path = Path(args.plot_output) if args.plot_output else None
        try:
            plot_path = generate_metrics_plot(rows, csv_path, output_path)
        except Exception as exc:
            print(f"[ERROR] Plot was not generated: {exc}", file=sys.stderr)
            return 1
        print(f"\n[OK] Metrics plot generated: {plot_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
