#!/usr/bin/env python3
"""
stress_test_translator.py

Stress/scale benchmark for a YAML -> Kathará translator.

What it measures:
- translation wall-clock time
- peak RSS memory of the translator process tree
- success/failure/timeout rate
- input YAML size
- optional generated laboratory size
- throughput in nodes/s and interfaces/s
- median / mean / p95 across repeated runs

The script intentionally benchmarks translation only. It does not start
Kathará containers unless the command passed with --command does so.

Example:
    python3 stress_test_translator.py \
        --command "python3 generate_lab.py {yaml}" \
        --sizes 10 50 100 250 500 1000 \
        --repeats 3 \
        --topology p2p \
        --cwd /path/to/Thesis \
        --generated-root labs

Placeholders available inside --command:
    {yaml}       absolute path of the generated YAML file
    {case_dir}   absolute path of the benchmark case directory
    {lab_name}   generated lab name
    {nodes}      total number of nodes in the test
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import shlex
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable, Optional

try:
    import psutil
except ImportError:
    psutil = None


DEFAULT_SIZES = [10, 50, 100, 250, 500, 1000]


@dataclass
class RunResult:
    topology: str
    nodes: int
    interfaces: int
    run: int
    input_bytes: int
    output_bytes: int
    wall_seconds: float
    peak_rss_mb: float
    exit_code: int
    timed_out: bool
    success: bool
    stdout_tail: str
    stderr_tail: str


def ip_from_index(index: int) -> str:
    """
    Return a host address inside 10.0.0.0/8.
    index=1 -> 10.0.0.1
    """
    if index < 1 or index >= (1 << 24) - 1:
        raise ValueError("Too many addresses requested for 10.0.0.0/8")

    a = (index >> 16) & 0xFF
    b = (index >> 8) & 0xFF
    c = index & 0xFF
    return f"10.{a}.{b}.{c}"


def p2p_network(index: int) -> tuple[str, str]:
    """
    Return two usable addresses from a unique /30 inside 10.0.0.0/8.
    index starts from 0.

    Example:
      index=0 -> 10.0.0.1, 10.0.0.2
      index=1 -> 10.0.0.5, 10.0.0.6
    """
    base = index * 4
    if base + 2 >= (1 << 24):
        raise ValueError("Too many /30 networks requested for 10.0.0.0/8")

    router_ip = ip_from_index(base + 1)
    host_ip = ip_from_index(base + 2)
    return router_ip, host_ip


def yaml_header(lab_name: str, topology: str, total_nodes: int) -> list[str]:
    return [
        f"lab_name: {lab_name}",
        f"description: \"Translator scalability test: {topology}, {total_nodes} nodes\"",
        "metadata:",
        "  version: \"2.0\"",
        "  author: \"stress_test_translator.py\"",
        "nodes:",
    ]


def generate_flat_yaml(total_nodes: int, lab_name: str) -> tuple[str, int]:
    """
    One router + N-1 hosts on a single large collision domain.

    This profile stresses:
    - a very large node collection
    - one very large collision domain
    - duplicate-address and subnet-consistency checks
    """
    if total_nodes < 2:
        raise ValueError("flat topology requires at least 2 nodes")

    lines = yaml_header(lab_name, "flat", total_nodes)

    lines += [
        "  r1:",
        "    type: router",
        "    interfaces:",
        "      - network: lan_scale",
        "        ip: 10.0.0.1/8",
    ]

    interfaces = 1

    for i in range(1, total_nodes):
        host_ip = ip_from_index(i + 1)
        lines += [
            f"  pc_{i}:",
            "    type: host",
            "    interfaces:",
            "      - network: lan_scale",
            f"        ip: {host_ip}/8",
            "    default_gateway: 10.0.0.1",
        ]
        interfaces += 1

    return "\n".join(lines) + "\n", interfaces


def generate_p2p_yaml(total_nodes: int, lab_name: str) -> tuple[str, int]:
    """
    One router + N-1 hosts, each on its own /30 collision domain.

    This profile stresses:
    - many distinct collision domains
    - many router interfaces
    - many subnet/gateway checks
    - larger lab.conf/startup output
    """
    if total_nodes < 2:
        raise ValueError("p2p topology requires at least 2 nodes")

    lines = yaml_header(lab_name, "p2p", total_nodes)

    # Build router first, with one interface per host.
    lines += [
        "  r1:",
        "    type: router",
        "    interfaces:",
    ]

    router_addresses = []

    for i in range(total_nodes - 1):
        router_ip, host_ip = p2p_network(i)
        network_name = f"net_{i + 1}"
        router_addresses.append((network_name, router_ip, host_ip))

        lines += [
            f"      - network: {network_name}",
            f"        ip: {router_ip}/30",
        ]

    interfaces = total_nodes - 1

    # Then one host for each /30.
    for i, (network_name, router_ip, host_ip) in enumerate(router_addresses, start=1):
        lines += [
            f"  pc_{i}:",
            "    type: host",
            "    interfaces:",
            f"      - network: {network_name}",
            f"        ip: {host_ip}/30",
            f"    default_gateway: {router_ip}",
        ]
        interfaces += 1

    return "\n".join(lines) + "\n", interfaces


def generate_yaml(topology: str, total_nodes: int, lab_name: str) -> tuple[str, int]:
    if topology == "flat":
        return generate_flat_yaml(total_nodes, lab_name)
    if topology == "p2p":
        return generate_p2p_yaml(total_nodes, lab_name)
    raise ValueError(f"Unsupported topology: {topology}")


def directory_size(path: Path) -> int:
    if not path.exists():
        return 0

    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            pass
    return total


def monitor_memory(pid: int, stop_event: threading.Event, result: dict) -> None:
    """
    Poll RSS for the process and all current descendants.
    Requires psutil. If unavailable, memory stays at 0.
    """
    if psutil is None:
        return

    peak = 0

    try:
        parent = psutil.Process(pid)
    except psutil.Error:
        return

    while not stop_event.is_set():
        rss = 0
        try:
            processes = [parent] + parent.children(recursive=True)
            for proc in processes:
                try:
                    rss += proc.memory_info().rss
                except psutil.Error:
                    pass
            peak = max(peak, rss)
        except psutil.Error:
            pass

        stop_event.wait(0.02)

    result["peak_rss_bytes"] = peak


def tail(text: str, max_chars: int = 1000) -> str:
    text = text.replace("\x00", "")
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def execute_once(
    command_template: str,
    yaml_path: Path,
    case_dir: Path,
    lab_name: str,
    nodes: int,
    topology: str,
    interfaces: int,
    run_number: int,
    cwd: Optional[Path],
    timeout: float,
    generated_root: Optional[Path],
) -> RunResult:
    command = command_template.format(
        yaml=shlex.quote(str(yaml_path.resolve())),
        case_dir=shlex.quote(str(case_dir.resolve())),
        lab_name=shlex.quote(lab_name),
        nodes=nodes,
    )

    start = time.perf_counter()

    proc = subprocess.Popen(
        command,
        cwd=str(cwd) if cwd else None,
        shell=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )

    memory_data = {"peak_rss_bytes": 0}
    stop_event = threading.Event()
    monitor = threading.Thread(
        target=monitor_memory,
        args=(proc.pid, stop_event, memory_data),
        daemon=True,
    )
    monitor.start()

    timed_out = False

    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True

        # Kill the complete process group on POSIX.
        try:
            os.killpg(proc.pid, 9)
        except Exception:
            proc.kill()

        stdout, stderr = proc.communicate()

    elapsed = time.perf_counter() - start

    stop_event.set()
    monitor.join(timeout=1)

    exit_code = proc.returncode if proc.returncode is not None else -999
    success = (exit_code == 0) and not timed_out

    output_bytes = 0
    if generated_root:
        lab_dir = generated_root / lab_name
        output_bytes = directory_size(lab_dir)

    return RunResult(
        topology=topology,
        nodes=nodes,
        interfaces=interfaces,
        run=run_number,
        input_bytes=yaml_path.stat().st_size,
        output_bytes=output_bytes,
        wall_seconds=elapsed,
        peak_rss_mb=memory_data["peak_rss_bytes"] / (1024 * 1024),
        exit_code=exit_code,
        timed_out=timed_out,
        success=success,
        stdout_tail=tail(stdout),
        stderr_tail=tail(stderr),
    )


def percentile(values: list[float], p: float) -> float:
    if not values:
        return math.nan

    values = sorted(values)

    if len(values) == 1:
        return values[0]

    rank = (len(values) - 1) * p
    low = math.floor(rank)
    high = math.ceil(rank)

    if low == high:
        return values[low]

    fraction = rank - low
    return values[low] * (1 - fraction) + values[high] * fraction


def write_runs_csv(path: Path, rows: list[RunResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    fields = list(asdict(rows[0]).keys()) if rows else [
        "topology", "nodes", "interfaces", "run", "input_bytes",
        "output_bytes", "wall_seconds", "peak_rss_mb", "exit_code",
        "timed_out", "success", "stdout_tail", "stderr_tail"
    ]

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def write_summary_csv(path: Path, rows: list[RunResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    groups: dict[tuple[str, int], list[RunResult]] = {}
    for row in rows:
        groups.setdefault((row.topology, row.nodes), []).append(row)

    fields = [
        "topology",
        "nodes",
        "interfaces",
        "runs",
        "successful_runs",
        "success_rate",
        "input_kb",
        "output_kb",
        "mean_seconds",
        "median_seconds",
        "p95_seconds",
        "min_seconds",
        "max_seconds",
        "mean_peak_rss_mb",
        "max_peak_rss_mb",
        "nodes_per_second",
        "interfaces_per_second",
    ]

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()

        for key in sorted(groups, key=lambda x: (x[0], x[1])):
            group = groups[key]
            successful = [x for x in group if x.success]
            source = successful or group

            times = [x.wall_seconds for x in source]
            rss = [x.peak_rss_mb for x in source]

            median_seconds = statistics.median(times) if times else math.nan
            nodes = group[0].nodes
            interfaces = group[0].interfaces

            writer.writerow({
                "topology": group[0].topology,
                "nodes": nodes,
                "interfaces": interfaces,
                "runs": len(group),
                "successful_runs": len(successful),
                "success_rate": len(successful) / len(group) if group else 0,
                "input_kb": group[0].input_bytes / 1024,
                "output_kb": max(x.output_bytes for x in group) / 1024,
                "mean_seconds": statistics.mean(times) if times else math.nan,
                "median_seconds": median_seconds,
                "p95_seconds": percentile(times, 0.95),
                "min_seconds": min(times) if times else math.nan,
                "max_seconds": max(times) if times else math.nan,
                "mean_peak_rss_mb": statistics.mean(rss) if rss else math.nan,
                "max_peak_rss_mb": max(rss) if rss else math.nan,
                "nodes_per_second": nodes / median_seconds if median_seconds > 0 else math.nan,
                "interfaces_per_second": interfaces / median_seconds if median_seconds > 0 else math.nan,
            })


def print_summary(rows: list[RunResult]) -> None:
    print("\n=== SCALABILITY SUMMARY ===")
    print(
        f"{'Topology':<10} {'Nodes':>7} {'Ifaces':>8} "
        f"{'OK':>7} {'Median(s)':>11} {'P95(s)':>10} "
        f"{'PeakMB':>10} {'Nodes/s':>10}"
    )
    print("-" * 84)

    groups: dict[tuple[str, int], list[RunResult]] = {}
    for row in rows:
        groups.setdefault((row.topology, row.nodes), []).append(row)

    for key in sorted(groups, key=lambda x: (x[0], x[1])):
        group = groups[key]
        successful = [x for x in group if x.success]
        source = successful or group

        times = [x.wall_seconds for x in source]
        med = statistics.median(times)
        p95 = percentile(times, 0.95)
        peak = max(x.peak_rss_mb for x in source)
        nodes_per_s = group[0].nodes / med if med > 0 else math.nan

        ok = f"{len(successful)}/{len(group)}"

        print(
            f"{group[0].topology:<10} "
            f"{group[0].nodes:>7} "
            f"{group[0].interfaces:>8} "
            f"{ok:>7} "
            f"{med:>11.4f} "
            f"{p95:>10.4f} "
            f"{peak:>10.1f} "
            f"{nodes_per_s:>10.1f}"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stress-test scalability of a YAML -> Kathará translator."
    )

    parser.add_argument(
        "--command",
        help=(
            "Translator command template. "
            "Example: 'python3 generate_lab.py {yaml}'. "
            "Not required with --generate-only."
        ),
    )

    parser.add_argument(
        "--sizes",
        nargs="+",
        type=int,
        default=DEFAULT_SIZES,
        help=f"Total node counts. Default: {' '.join(map(str, DEFAULT_SIZES))}",
    )

    parser.add_argument(
        "--repeats",
        type=int,
        default=3,
        help="Number of measured runs for each size. Default: 3",
    )

    parser.add_argument(
        "--warmup",
        type=int,
        default=1,
        help="Warm-up translations excluded from statistics. Default: 1",
    )

    parser.add_argument(
        "--topology",
        choices=["flat", "p2p", "both"],
        default="both",
        help="Topology profile to benchmark. Default: both",
    )

    parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="Timeout for a single translation, in seconds. Default: 120",
    )

    parser.add_argument(
        "--cwd",
        type=Path,
        help="Working directory used to launch the translator.",
    )

    parser.add_argument(
        "--generated-root",
        type=Path,
        help=(
            "Optional root containing generated lab folders named after lab_name. "
            "Example: --generated-root labs"
        ),
    )

    parser.add_argument(
        "--results-dir",
        type=Path,
        default=Path("stress_results"),
        help="Directory for YAML inputs and CSV results. Default: stress_results",
    )

    parser.add_argument(
        "--generate-only",
        action="store_true",
        help="Generate stress-test YAML files without executing the translator.",
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not args.generate_only and not args.command:
        print("ERROR: --command is required unless --generate-only is used.", file=sys.stderr)
        return 2

    if args.repeats < 1:
        print("ERROR: --repeats must be >= 1", file=sys.stderr)
        return 2

    if args.warmup < 0:
        print("ERROR: --warmup must be >= 0", file=sys.stderr)
        return 2

    for n in args.sizes:
        if n < 2:
            print(f"ERROR: every test size must be >= 2; got {n}", file=sys.stderr)
            return 2

    results_dir = args.results_dir.resolve()
    inputs_dir = results_dir / "inputs"
    cases_dir = results_dir / "cases"
    inputs_dir.mkdir(parents=True, exist_ok=True)
    cases_dir.mkdir(parents=True, exist_ok=True)

    cwd = args.cwd.resolve() if args.cwd else None

    generated_root = args.generated_root
    if generated_root:
        if not generated_root.is_absolute():
            generated_root = ((cwd or Path.cwd()) / generated_root).resolve()

    topologies = ["flat", "p2p"] if args.topology == "both" else [args.topology]

    all_results: list[RunResult] = []

    if psutil is None and not args.generate_only:
        print(
            "[WARNING] psutil is not installed: peak RSS will be reported as 0 MB.\n"
            "          Install it with: python3 -m pip install psutil"
        )

    for topology in topologies:
        print(f"\n=== TOPOLOGY: {topology} ===")

        for nodes in args.sizes:
            lab_name = f"scale_{topology}_{nodes}"
            yaml_text, interfaces = generate_yaml(topology, nodes, lab_name)
            yaml_path = inputs_dir / f"{lab_name}.yaml"
            yaml_path.write_text(yaml_text, encoding="utf-8")

            input_kb = yaml_path.stat().st_size / 1024
            print(
                f"\n[{lab_name}] nodes={nodes}, interfaces={interfaces}, "
                f"input={input_kb:.1f} KB"
            )

            if args.generate_only:
                continue

            case_dir = cases_dir / lab_name
            case_dir.mkdir(parents=True, exist_ok=True)

            # Warm-up runs are intentionally ignored.
            for warm in range(args.warmup):
                print(f"  warm-up {warm + 1}/{args.warmup} ...", end="", flush=True)
                warm_result = execute_once(
                    command_template=args.command,
                    yaml_path=yaml_path,
                    case_dir=case_dir,
                    lab_name=lab_name,
                    nodes=nodes,
                    topology=topology,
                    interfaces=interfaces,
                    run_number=0,
                    cwd=cwd,
                    timeout=args.timeout,
                    generated_root=generated_root,
                )
                status = "OK" if warm_result.success else "FAIL"
                print(f" {status} ({warm_result.wall_seconds:.4f}s)")

            for run_number in range(1, args.repeats + 1):
                print(f"  run {run_number}/{args.repeats} ...", end="", flush=True)

                result = execute_once(
                    command_template=args.command,
                    yaml_path=yaml_path,
                    case_dir=case_dir,
                    lab_name=lab_name,
                    nodes=nodes,
                    topology=topology,
                    interfaces=interfaces,
                    run_number=run_number,
                    cwd=cwd,
                    timeout=args.timeout,
                    generated_root=generated_root,
                )

                all_results.append(result)

                if result.timed_out:
                    status = "TIMEOUT"
                elif result.success:
                    status = "OK"
                else:
                    status = f"FAIL({result.exit_code})"

                print(
                    f" {status} | {result.wall_seconds:.4f}s | "
                    f"peak={result.peak_rss_mb:.1f} MB"
                )

    if args.generate_only:
        print(f"\nGenerated YAML files: {inputs_dir}")
        return 0

    runs_csv = results_dir / "runs.csv"
    summary_csv = results_dir / "summary.csv"

    write_runs_csv(runs_csv, all_results)
    write_summary_csv(summary_csv, all_results)
    print_summary(all_results)

    print(f"\nDetailed results: {runs_csv}")
    print(f"Summary results : {summary_csv}")
    print(f"Generated inputs: {inputs_dir}")

    failures = [x for x in all_results if not x.success]
    if failures:
        print(f"\nWARNING: {len(failures)} measured run(s) failed or timed out.")
        print("Inspect runs.csv for stdout/stderr tails.")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
