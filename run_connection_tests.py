#!/usr/bin/env python3
"""
run_connection_tests.py

Measures ICMP RTT, TCP handshake time, and application response time from
Kathara clients. Clients run in parallel for every round to model legitimate
hosts attempting connections simultaneously.

Interactive usage:
    ./run_connection_tests.py

Non-interactive usage:
    ./run_connection_tests.py --lab Test1 --target server --port 9000 \
        --scenario baseline --attempts 10 --timeout 2 --delay 1 --non-interactive

Output:
- results/connection_results_<scenario>_<timestamp>.csv
- logs/connection_tests_<scenario>_<timestamp>.log
- logs/<client>_<scenario>_<timestamp>.log
"""

from __future__ import annotations

import argparse
import base64
import csv
import ipaddress
import json
import re
import shlex
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable


@dataclass
class LabContext:
    project_dir: Path
    lab_dir: Path | None


@dataclass
class TestConfig:
    target_input: str
    target_resolved: str
    port: int
    clients: list[str]
    scenario: str
    attempts: int
    timeout: int
    delay: float
    lab_dir: Path | None


def now_run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def prompt_value(label: str, default: str | None = None, required: bool = False) -> str:
    while True:
        if default is None or default == "":
            raw = input(f"{label}: ").strip()
        else:
            raw = input(f"{label} [{default}]: ").strip()
        value = raw if raw else (default or "")
        if value or not required:
            return value
        print("A value is required.")


def parse_clients(value: str) -> list[str]:
    clients = [part.strip() for part in re.split(r"[,\s]+", value.strip()) if part.strip()]
    if not clients:
        raise ValueError("Specify at least one client, for example: pc_a,pc_b,pc_c")
    return clients


def parse_int(value: str, label: str, minimum: int = 1) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise ValueError(f"{label} must be an integer.") from exc
    if number < minimum:
        raise ValueError(f"{label} must be >= {minimum}.")
    return number


def parse_float(value: str, label: str, minimum: float = 0.0) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise ValueError(f"{label} must be a number.") from exc
    if number < minimum:
        raise ValueError(f"{label} must be >= {minimum}.")
    return number


def is_ip_address(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def list_docker_containers() -> list[str]:
    result = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Unable to list Docker containers: {result.stderr.strip()}")
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def extract_pc_nodes_from_container_names(containers: Iterable[str]) -> list[str]:
    pattern = re.compile(r"^kathara_.*_(pc_[A-Za-z0-9]+)_")
    result: list[str] = []
    seen = set()
    for container in containers:
        match = pattern.search(container)
        if not match:
            continue
        node = match.group(1)
        if node not in seen:
            seen.add(node)
            result.append(node)
    return sorted(result)


def read_lab_nodes_from_lab_conf(lab_dir: Path) -> list[str]:
    lab_conf = lab_dir / "lab.conf"
    if not lab_conf.exists():
        return []
    nodes: list[str] = []
    seen = set()
    pattern = re.compile(r"^([A-Za-z0-9_.-]+)\[")
    for line in lab_conf.read_text(encoding="utf-8", errors="ignore").splitlines():
        match = pattern.match(line.strip())
        if not match:
            continue
        name = match.group(1)
        if name not in seen:
            seen.add(name)
            nodes.append(name)
    return nodes


def discover_labs(project_dir: Path) -> list[Path]:
    labs_dir = project_dir / "labs"
    if not labs_dir.exists():
        return []
    labs = [path for path in labs_dir.iterdir() if path.is_dir() and (path / "lab.conf").exists()]
    return sorted(labs, key=lambda p: p.name.lower())


def current_lab_dir(cwd: Path) -> Path | None:
    if (cwd / "lab.conf").exists():
        return cwd
    return None


def infer_project_dir(cwd: Path) -> Path:
    if (cwd / "labs").exists():
        return cwd
    if cwd.parent and (cwd.parent / "labs").exists():
        return cwd.parent
    return cwd


def choose_lab_interactively(labs: list[Path]) -> Path:
    if len(labs) == 1:
        print(f"[INFO] Using detected lab: {labs[0].name}")
        return labs[0]

    print("Labs available in ./labs:")
    for index, lab in enumerate(labs, start=1):
        print(f"  {index}) {lab.name}")

    while True:
        raw = input(f"Select a lab [1-{len(labs)}]: ").strip()
        try:
            choice = int(raw)
        except ValueError:
            print("Invalid selection.")
            continue
        if 1 <= choice <= len(labs):
            return labs[choice - 1]
        print("Selection out of range.")


def resolve_lab_context(args: argparse.Namespace, interactive: bool) -> LabContext:
    cwd = Path.cwd()
    current = current_lab_dir(cwd)
    project_dir = infer_project_dir(cwd)

    if args.lab:
        candidate = Path(args.lab)
        if not candidate.is_absolute():
            if candidate.exists():
                candidate = candidate.resolve()
            else:
                candidate = (project_dir / "labs" / args.lab).resolve()
        if not candidate.exists() or not (candidate / "lab.conf").exists():
            raise ValueError(f"Lab not found or invalid: {candidate}")
        return LabContext(project_dir=project_dir, lab_dir=candidate)

    if current:
        return LabContext(project_dir=project_dir, lab_dir=current)

    labs = discover_labs(project_dir)
    if not labs:
        return LabContext(project_dir=project_dir, lab_dir=None)
    if len(labs) == 1:
        return LabContext(project_dir=project_dir, lab_dir=labs[0])
    if interactive:
        return LabContext(project_dir=project_dir, lab_dir=choose_lab_interactively(labs))

    raise ValueError("Multiple labs exist in ./labs: use --lab <lab_name>.")


def default_clients_from_docker() -> str:
    try:
        containers = list_docker_containers()
    except Exception:
        return ""
    clients = extract_pc_nodes_from_container_names(containers)
    return " ".join(clients)


def default_clients_from_lab(lab_dir: Path | None) -> str:
    docker_clients = default_clients_from_docker()
    if docker_clients:
        return docker_clients
    if lab_dir:
        nodes = read_lab_nodes_from_lab_conf(lab_dir)
        candidates = [node for node in nodes if re.fullmatch(r"pc_[A-Za-z0-9]+", node)]
        if candidates:
            return " ".join(candidates)
    return "pc_a pc_b pc_c"


def read_ip_from_startup(startup_path: Path) -> str | None:
    if not startup_path.exists():
        return None
    pattern = re.compile(r"^ip\s+address\s+add\s+([^\s/]+)(?:/\d+)?\s+dev\s+eth\d+")
    for line in startup_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        match = pattern.match(line.strip())
        if match:
            return match.group(1)
    return None


def resolve_target(target: str, lab_dir: Path | None) -> str:
    target = target.strip()
    if is_ip_address(target):
        return target

    if lab_dir:
        startup_path = lab_dir / f"{target}.startup"
        ip_addr = read_ip_from_startup(startup_path)
        if ip_addr:
            print(f"[INFO] Target '{target}' resolved from {startup_path}: {ip_addr}")
            return ip_addr
        raise ValueError(
            f"Target '{target}' could not be resolved. File missing or without an IP: {startup_path}. "
            "Use an IP address or select the correct lab with --lab."
        )

    raise ValueError(
        f"Target '{target}' is not an IP address and the lab could not be determined. "
        "Run the script inside labs/<lab_name>/ or use --lab <lab_name>."
    )


def find_kathara_container(node: str, containers: Iterable[str]) -> str | None:
    pattern = re.compile(rf"^kathara_.*_{re.escape(node)}_")
    matches = [container for container in containers if pattern.search(container)]
    if not matches:
        return None
    return matches[0]


def write_log(log_path: Path, message: str, also_stdout: bool = True) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(message + "\n")
    if also_stdout:
        print(message)


CONTAINER_MEASUREMENT_SCRIPT = r'''
import json
import re
import socket
import subprocess
import sys
import time

target = sys.argv[1]
port = int(sys.argv[2])
timeout = float(sys.argv[3])
result = {
    "icmp_status": "FAIL",
    "icmp_rtt_ms": None,
    "icmp_error": "not_executed",
    "tcp_status": "FAIL",
    "tcp_connect_ms": None,
    "tcp_error": "not_executed",
    "application_status": "FAIL",
    "application_response_ms": None,
    "request_completion_ms": None,
    "application_error": "not_executed",
}

# ping measures ICMP RTT in the Kathara client network namespace.
try:
    ping = subprocess.run(
        ["ping", "-n", "-c", "1", "-W", str(max(1, int(timeout))), target],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout + 1,
        check=False,
    )
    ping_output = ping.stdout or ""
    match = re.search(r"time[=<]\s*([0-9.]+)\s*ms", ping_output)
    if ping.returncode == 0 and match:
        result["icmp_status"] = "OK"
        result["icmp_rtt_ms"] = round(float(match.group(1)), 3)
        result["icmp_error"] = ""
    elif "100% packet loss" in ping_output or "0 received" in ping_output:
        result["icmp_error"] = "icmp_timeout"
    else:
        result["icmp_error"] = "icmp_unreachable_or_failed"
except FileNotFoundError:
    result["icmp_error"] = "ping_not_available"
except subprocess.TimeoutExpired:
    result["icmp_error"] = "icmp_timeout"
except Exception as exc:
    result["icmp_error"] = "icmp_internal_error:" + type(exc).__name__

# Connection and response timings are collected directly in the client.
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.settimeout(timeout)
request_started = time.perf_counter()
try:
    connect_started = time.perf_counter()
    sock.connect((target, port))
    result["tcp_connect_ms"] = round((time.perf_counter() - connect_started) * 1000, 3)
    result["tcp_status"] = "OK"
    result["tcp_error"] = ""

    application_started = time.perf_counter()
    sock.sendall(b"1\n")
    response = bytearray()
    while b"pong" not in response:
        chunk = sock.recv(4096)
        if not chunk:
            break
        response.extend(chunk)
    if b"pong" in response:
        completed = time.perf_counter()
        result["application_status"] = "OK"
        result["application_response_ms"] = round((completed - application_started) * 1000, 3)
        result["request_completion_ms"] = round((completed - request_started) * 1000, 3)
        result["application_error"] = ""
    else:
        result["application_error"] = "pong_not_received"
except socket.timeout:
    if result["tcp_status"] == "OK":
        result["application_error"] = "application_timeout"
    else:
        result["tcp_error"] = "tcp_connect_timeout"
        result["application_error"] = "tcp_connect_timeout"
except OSError as exc:
    error = "socket_error:" + (str(exc.errno) if exc.errno is not None else type(exc).__name__)
    if result["tcp_status"] == "OK":
        result["application_error"] = error
    else:
        result["tcp_error"] = error
        result["application_error"] = error
except Exception as exc:
    result["application_error"] = "internal_error:" + type(exc).__name__
finally:
    sock.close()

print(json.dumps(result, separators=(",", ":")))
'''.strip()


def run_container_measurements(container: str, target: str, port: int, timeout: int) -> tuple[dict[str, object], str]:
    """Runs all measurements in the client, excluding host-side orchestration overhead."""
    encoded_script = base64.b64encode(CONTAINER_MEASUREMENT_SCRIPT.encode("utf-8")).decode("ascii")
    launcher = "import base64;exec(base64.b64decode('" + encoded_script + "'))"
    command = [
        "docker", "exec", container, "python3", "-c", launcher,
        target, str(port), str(timeout),
    ]
    try:
        completed = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=(timeout * 3) + 5,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        raise RuntimeError("docker_exec_timeout") from exc

    output = completed.stdout or ""
    if completed.returncode != 0:
        raise RuntimeError(f"container_measurement_failed:{output.strip() or completed.returncode}")
    try:
        payload = json.loads(output.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"invalid_measurement_output:{output.strip()}") from exc
    return payload, output.strip()


def run_one_attempt(
    client: str,
    container: str,
    request_index: int,
    config: TestConfig,
    experiment_started_at: str,
    experiment_started_perf: float,
) -> dict[str, str]:
    start = time.perf_counter()
    measurements, output = run_container_measurements(
        container=container,
        target=config.target_resolved,
        port=config.port,
        timeout=config.timeout,
    )
    host_command_completion_ms = (time.perf_counter() - start) * 1000
    status = str(measurements["application_status"])
    error = str(measurements["application_error"])

    def value(name: str) -> str:
        raw = measurements.get(name)
        return "" if raw is None else str(raw)

    return {
        "timestamp": now_iso(),
        "experiment_started_at": experiment_started_at,
        "experiment_elapsed_ms": f"{(time.perf_counter() - experiment_started_perf) * 1000:.2f}",
        "scenario": config.scenario,
        "client": client,
        "request_index": str(request_index),
        "target_input": config.target_input,
        "target": config.target_resolved,
        "port": str(config.port),
        "status": status,
        "icmp_status": value("icmp_status"),
        "icmp_rtt_ms": value("icmp_rtt_ms"),
        "icmp_error": value("icmp_error"),
        "tcp_status": value("tcp_status"),
        "tcp_connect_ms": value("tcp_connect_ms"),
        "tcp_error": value("tcp_error"),
        "application_status": value("application_status"),
        "application_response_ms": value("application_response_ms"),
        "request_completion_ms": value("request_completion_ms"),
        "host_command_completion_ms": f"{host_command_completion_ms:.2f}",
        "application_error": error,
        "error": error,
        "debug_output": output,
    }


def build_config(args: argparse.Namespace, context: LabContext) -> TestConfig:
    interactive = not args.non_interactive

    target_input = args.target
    port_value = str(args.port) if args.port is not None else ""
    clients_value = args.clients or ""
    scenario = args.scenario or "baseline"
    attempts_value = str(args.attempts) if args.attempts is not None else "10"
    timeout_value = str(args.timeout) if args.timeout is not None else "3"
    delay_value = str(args.delay) if args.delay is not None else "1"

    if interactive:
        print("Network and application measurement configuration")
        print("The test measures ICMP RTT, TCP connection time, and application response time inside each client.")
        print("All clients run simultaneously for each round.")
        print("Press ENTER to accept a displayed default value.\n")

        if context.lab_dir:
            print(f"[INFO] Selected lab: {context.lab_dir.name}")

        target_input = prompt_value("Target server IP/host", target_input, required=True)
        port_value = prompt_value("Service TCP port", port_value or "9000", required=True)

        clients_default = clients_value or default_clients_from_lab(context.lab_dir)
        if clients_default:
            print(f"Automatically detected clients: {clients_default}")
        clients_value = prompt_value("Kathara clients to use", clients_default, required=True)

        scenario = prompt_value("Scenario", scenario or "baseline", required=True)
        attempts_value = prompt_value("Simultaneous rounds per client", attempts_value or "10", required=True)
        timeout_value = prompt_value("Timeout in seconds", timeout_value or "3", required=True)
        delay_value = prompt_value("Delay between simultaneous rounds", delay_value or "1", required=True)
    else:
        missing = []
        if not target_input:
            missing.append("--target")
        if not port_value:
            missing.append("--port")
        if not clients_value:
            clients_value = default_clients_from_lab(context.lab_dir)
        if not clients_value:
            missing.append("--clients")
        if missing:
            raise ValueError("Missing in --non-interactive mode: " + ", ".join(missing))

    if target_input is None or not target_input.strip():
        raise ValueError("Missing target.")

    target_resolved = resolve_target(target_input, context.lab_dir)

    return TestConfig(
        target_input=target_input.strip(),
        target_resolved=target_resolved,
        port=parse_int(port_value, "Port", minimum=1),
        clients=parse_clients(clients_value),
        scenario=scenario.strip() or "baseline",
        attempts=parse_int(attempts_value, "Attempts", minimum=1),
        timeout=parse_int(timeout_value, "Timeout", minimum=1),
        delay=parse_float(delay_value, "Delay", minimum=0.0),
        lab_dir=context.lab_dir,
    )


def run_tests(config: TestConfig, output_dir: Path) -> Path:
    run_id = now_run_id()
    results_dir = output_dir / "results"
    logs_dir = output_dir / "logs"
    results_dir.mkdir(exist_ok=True)
    logs_dir.mkdir(exist_ok=True)
    csv_path = results_dir / f"connection_results_{config.scenario}_{run_id}.csv"
    main_log = logs_dir / f"connection_tests_{config.scenario}_{run_id}.log"

    write_log(main_log, f"[INFO] Output dir: {output_dir}")
    if config.lab_dir:
        write_log(main_log, f"[INFO] Selected lab: {config.lab_dir}")
    write_log(main_log, f"[INFO] Scenario: {config.scenario}")
    write_log(main_log, f"[INFO] Target input: {config.target_input}")
    write_log(main_log, f"[INFO] Resolved target: {config.target_resolved}:{config.port}")
    write_log(main_log, f"[INFO] Client: {' '.join(config.clients)}")
    write_log(main_log, f"[INFO] Simultaneous rounds per client: {config.attempts}")
    write_log(main_log, f"[INFO] Timeout: {config.timeout}s")
    write_log(main_log, f"[INFO] Delay between rounds: {config.delay}s")
    write_log(main_log, f"[INFO] Output CSV: {csv_path}")
    write_log(main_log, "")

    experiment_started_at = now_iso()
    experiment_started_perf = time.perf_counter()

    containers = list_docker_containers()
    client_to_container: dict[str, str] = {}
    for client in config.clients:
        container = find_kathara_container(client, containers)
        client_log = logs_dir / f"{client}_{config.scenario}_{run_id}.log"
        if not container:
            message = f"[ERROR] Container not found for node: {client}"
            write_log(main_log, message)
            write_log(client_log, message, also_stdout=False)
            continue
        client_to_container[client] = container
        write_log(main_log, f"[OK] Node {client} -> container {container}")
        write_log(client_log, f"[OK] Node {client} -> container {container}", also_stdout=False)

    if not client_to_container:
        raise ValueError("No valid client containers were found.")

    fieldnames = [
        "timestamp", "experiment_started_at", "experiment_elapsed_ms",
        "scenario", "client", "request_index", "target_input", "target", "port",
        "status", "icmp_status", "icmp_rtt_ms", "icmp_error",
        "tcp_status", "tcp_connect_ms", "tcp_error",
        "application_status", "application_response_ms", "request_completion_ms",
        "host_command_completion_ms", "application_error", "error",
    ]

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for attempt in range(1, config.attempts + 1):
            write_log(main_log, f"[ROUND {attempt}/{config.attempts}] Starting simultaneous connections")
            rows: list[dict[str, str]] = []
            with ThreadPoolExecutor(max_workers=len(client_to_container)) as executor:
                future_map = {
                    executor.submit(
                        run_one_attempt,
                        client,
                        container,
                        attempt,
                        config,
                        experiment_started_at,
                        experiment_started_perf,
                    ): client
                    for client, container in client_to_container.items()
                }
                for future in as_completed(future_map):
                    client = future_map[future]
                    client_log = logs_dir / f"{client}_{config.scenario}_{run_id}.log"
                    try:
                        row = future.result()
                    except Exception as exc:
                        row = {
                            "timestamp": now_iso(),
                            "experiment_started_at": experiment_started_at,
                            "experiment_elapsed_ms": f"{(time.perf_counter() - experiment_started_perf) * 1000:.2f}",
                            "scenario": config.scenario,
                            "client": client,
                            "request_index": str(attempt),
                            "target_input": config.target_input,
                            "target": config.target_resolved,
                            "port": str(config.port),
                            "status": "FAIL",
                            "icmp_status": "FAIL",
                            "icmp_rtt_ms": "",
                            "icmp_error": "measurement_not_completed",
                            "tcp_status": "FAIL",
                            "tcp_connect_ms": "",
                            "tcp_error": "measurement_not_completed",
                            "application_status": "FAIL",
                            "application_response_ms": "",
                            "request_completion_ms": "",
                            "host_command_completion_ms": "",
                            "application_error": f"internal_error:{exc}",
                            "error": f"internal_error:{exc}",
                            "debug_output": "",
                        }
                    rows.append(row)
                    line = (
                        f"[{client}][{attempt}/{config.attempts}] {row['status']} "
                        f"icmp={row['icmp_rtt_ms'] or 'N/D'}ms "
                        f"tcp={row['tcp_connect_ms'] or 'N/D'}ms "
                        f"app={row['application_response_ms'] or 'N/D'}ms "
                        f"error={row['error']}"
                    )
                    write_log(main_log, line)
                    write_log(client_log, line, also_stdout=False)
                    if row.get("debug_output"):
                        write_log(client_log, f"[DEBUG] measurement output: {row['debug_output']}", also_stdout=False)

            for row in sorted(rows, key=lambda item: item["client"]):
                row_for_csv = {key: row.get(key, "") for key in fieldnames}
                writer.writerow(row_for_csv)
            f.flush()

            if attempt < config.attempts and config.delay > 0:
                time.sleep(config.delay)

    write_log(main_log, "")
    write_log(main_log, "[OK] Tests completed.")
    write_log(main_log, f"[OK] CSV: {csv_path}")
    write_log(main_log, f"[OK] Main log: {main_log}")
    return csv_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Measure ICMP RTT, TCP connection time, and application response time from Kathara nodes."
    )
    parser.add_argument("--lab", help="Lab name in ./labs or a lab path")
    parser.add_argument("--target", help="Target server IP or node name, for example 10.0.20.10 or server")
    parser.add_argument("--port", type=int, help="Service TCP port, for example 9000")
    parser.add_argument(
        "--clients",
        help="Kathara clients separated by commas or spaces. If omitted, pc_* nodes are detected automatically.",
    )
    parser.add_argument("--scenario", default="baseline", help="Scenario name, e.g. baseline or dos")
    parser.add_argument("--attempts", type=int, default=10, help="Number of simultaneous rounds")
    parser.add_argument("--timeout", type=int, default=3, help="Timeout for each measurement in seconds")
    parser.add_argument("--delay", type=float, default=1.0, help="Pause between simultaneous rounds")
    parser.add_argument("--non-interactive", action="store_true", help="Do not prompt; requires at least target and port")
    return parser.parse_args()


def main() -> int:
    try:
        args = parse_args()
        interactive = not args.non_interactive
        context = resolve_lab_context(args, interactive=interactive)
        config = build_config(args, context)
        output_dir = Path.cwd()
        csv_path = run_tests(config, output_dir)
        print("")
        print("To display the report:")
        print(f"  ./show_connection_results.py {shlex.quote(str(csv_path))}")
        print("To generate the metric plots as well:")
        print(f"  ./show_connection_results.py {shlex.quote(str(csv_path))} --plot")
        return 0
    except KeyboardInterrupt:
        print("\n[INTERRUPTED] Test interrupted by the user.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
