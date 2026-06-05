#!/usr/bin/env python3
"""
run_connection_tests.py

Script autonomo per eseguire test di connessione TCP via nc tra i nodi
Kathara e un servizio esposto da un server.

Uso tipico, dalla cartella del laboratorio:
    ./run_connection_tests.py

Uso non interattivo:
    ./run_connection_tests.py --target 10.0.20.10 --port 9000 --clients pc_a,pc_b,pc_c \
        --scenario baseline --attempts 10 --timeout 3 --delay 1 --payload $'1\n' --expect pong

Output:
- results/connection_results_<scenario>_<timestamp>.csv
- results/responses_<scenario>_<timestamp>/...
- logs/connection_tests_<scenario>_<timestamp>.log
- logs/<client>_<scenario>_<timestamp>.log
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


@dataclass
class TestConfig:
    target: str
    port: int
    clients: list[str]
    scenario: str
    attempts: int
    timeout: int
    delay: float
    payload: str
    expect: str


def now_run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def prompt_value(label: str, default: str | None = None, required: bool = False) -> str:
    while True:
        if default is None or default == "":
            raw = input(f"{label}: ").strip()
        else:
            raw = input(f"{label} [{default}]: ").strip()
        value = raw if raw else (default or "")
        if value or not required:
            return value
        print("Valore obbligatorio.")


def parse_clients(value: str) -> list[str]:
    clients = [part.strip() for part in re.split(r"[,\s]+", value.strip()) if part.strip()]
    if not clients:
        raise ValueError("Devi indicare almeno un client, ad esempio: pc_a,pc_b,pc_c")
    return clients


def parse_int(value: str, label: str, minimum: int = 1) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise ValueError(f"{label} deve essere un numero intero.") from exc
    if number < minimum:
        raise ValueError(f"{label} deve essere >= {minimum}.")
    return number


def parse_float(value: str, label: str, minimum: float = 0.0) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise ValueError(f"{label} deve essere un numero.") from exc
    if number < minimum:
        raise ValueError(f"{label} deve essere >= {minimum}.")
    return number


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


def extract_pc_nodes_from_container_names(containers: Iterable[str]) -> list[str]:
    """
    Estrae automaticamente i nodi client dai container Kathara attivi.

    Formato atteso del nome container Kathara:
        kathara_<lab>_<node>_<suffix>

    Sono considerati client solo i nodi con nome:
        pc_<lettera_o_numero>

    Esempi validi:
        kathara_luke-xxx_pc_a_yyy  -> pc_a
        kathara_luke-xxx_pc_b_yyy  -> pc_b
        kathara_luke-xxx_pc_1_yyy  -> pc_1
    """
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


def default_clients_from_docker() -> str:
    try:
        containers = list_docker_containers()
    except Exception:
        return ""

    clients = extract_pc_nodes_from_container_names(containers)
    return " ".join(clients)


def default_clients_from_lab(lab_dir: Path) -> str:
    docker_clients = default_clients_from_docker()
    if docker_clients:
        return docker_clients

    nodes = read_lab_nodes_from_lab_conf(lab_dir)
    candidates = [
        n for n in nodes
        if re.fullmatch(r"pc_[A-Za-z0-9]+", n)
    ]
    return " ".join(candidates) if candidates else "pc_a pc_b pc_c"


def list_docker_containers() -> list[str]:
    result = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Impossibile leggere i container Docker: {result.stderr.strip()}")
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def find_kathara_container(node: str, containers: Iterable[str]) -> str | None:
    pattern = re.compile(rf"^kathara_.*_{re.escape(node)}_")
    for container in containers:
        if pattern.search(container):
            return container
    return None


def write_log(log_path: Path, message: str, also_stdout: bool = True) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(message + "\n")
    if also_stdout:
        print(message)


def run_nc_in_container(
    container: str,
    target: str,
    port: int,
    timeout: int,
    payload: str,
    response_file: Path,
) -> tuple[int, str]:
    response_file.parent.mkdir(parents=True, exist_ok=True)

    command = [
        "docker", "exec",
        "-e", f"PAYLOAD={payload}",
        "-e", f"TARGET={target}",
        "-e", f"PORT={port}",
        "-e", f"TIMEOUT={timeout}",
        container,
        "sh", "-lc",
        'printf "%s" "$PAYLOAD" | timeout "$TIMEOUT" nc -w "$TIMEOUT" "$TARGET" "$PORT"',
    ]

    completed = subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )

    response_file.write_text(completed.stdout or "", encoding="utf-8", errors="ignore")
    return completed.returncode, completed.stdout or ""


def build_config(args: argparse.Namespace, lab_dir: Path) -> TestConfig:
    interactive = not args.non_interactive

    target = args.target
    port_value = str(args.port) if args.port is not None else ""
    clients_value = args.clients or ""
    scenario = args.scenario or "baseline"
    attempts_value = str(args.attempts) if args.attempts is not None else "10"
    timeout_value = str(args.timeout) if args.timeout is not None else "3"
    delay_value = str(args.delay) if args.delay is not None else "1"
    payload = args.payload if args.payload is not None else "1\n"
    expect = args.expect if args.expect is not None else ""

    if interactive:
        print("Configurazione test connessioni via nc")
        print("Premi INVIO per accettare il valore predefinito, quando presente.\n")
        target = prompt_value("IP/host server target", target, required=True)
        port_value = prompt_value("Porta TCP servizio", port_value or "9000", required=True)
        clients_default = clients_value or default_clients_from_lab(lab_dir)
        if clients_default:
            print(f"Client rilevati automaticamente: {clients_default}")
        clients_value = prompt_value("Client Kathara da usare", clients_default, required=True)
        scenario = prompt_value("Scenario", scenario or "baseline", required=True)
        attempts_value = prompt_value("Tentativi per client", attempts_value or "10", required=True)
        timeout_value = prompt_value("Timeout secondi", timeout_value or "3", required=True)
        delay_value = prompt_value("Delay tra tentativi", delay_value or "1", required=True)
        payload = prompt_value("Payload da inviare", payload, required=False)
        expect = prompt_value("Testo atteso nella risposta", expect, required=False)
    else:
        missing = []
        if not target:
            missing.append("--target")
        if not port_value:
            missing.append("--port")
        if not clients_value:
            clients_value = default_clients_from_lab(lab_dir)
        if not clients_value:
            missing.append("--clients")
        if missing:
            raise ValueError("In modalità --non-interactive mancano: " + ", ".join(missing))

    if target is None or not target.strip():
        raise ValueError("Target mancante.")

    return TestConfig(
        target=target.strip(),
        port=parse_int(port_value, "Porta", minimum=1),
        clients=parse_clients(clients_value),
        scenario=scenario.strip() or "baseline",
        attempts=parse_int(attempts_value, "Tentativi", minimum=1),
        timeout=parse_int(timeout_value, "Timeout", minimum=1),
        delay=parse_float(delay_value, "Delay", minimum=0.0),
        payload=payload,
        expect=expect,
    )


def run_tests(config: TestConfig, lab_dir: Path) -> Path:
    run_id = now_run_id()
    results_dir = lab_dir / "results"
    logs_dir = lab_dir / "logs"
    responses_dir = results_dir / f"responses_{config.scenario}_{run_id}"

    results_dir.mkdir(exist_ok=True)
    logs_dir.mkdir(exist_ok=True)
    responses_dir.mkdir(exist_ok=True)

    csv_path = results_dir / f"connection_results_{config.scenario}_{run_id}.csv"
    main_log = logs_dir / f"connection_tests_{config.scenario}_{run_id}.log"

    write_log(main_log, f"[INFO] Lab dir: {lab_dir}")
    write_log(main_log, f"[INFO] Scenario: {config.scenario}")
    write_log(main_log, f"[INFO] Target: {config.target}:{config.port}")
    write_log(main_log, f"[INFO] Client: {' '.join(config.clients)}")
    write_log(main_log, f"[INFO] Tentativi per client: {config.attempts}")
    write_log(main_log, f"[INFO] Timeout: {config.timeout}s")
    write_log(main_log, f"[INFO] Delay: {config.delay}s")
    write_log(main_log, f"[INFO] Output CSV: {csv_path}")
    write_log(main_log, f"[INFO] Risposte complete: {responses_dir}")
    write_log(main_log, "")

    containers = list_docker_containers()

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "timestamp", "scenario", "client", "attempt", "target", "port",
                "status", "elapsed_ms", "error", "response_file",
            ],
        )
        writer.writeheader()

        for client in config.clients:
            container = find_kathara_container(client, containers)
            client_log = logs_dir / f"{client}_{config.scenario}_{run_id}.log"

            if not container:
                message = f"[ERRORE] Container non trovato per nodo: {client}"
                write_log(main_log, message)
                write_log(client_log, message, also_stdout=False)
                continue

            write_log(main_log, f"[OK] Nodo {client} -> container {container}")
            write_log(client_log, f"[OK] Nodo {client} -> container {container}", also_stdout=False)

            for attempt in range(1, config.attempts + 1):
                response_file = responses_dir / f"{client}_attempt_{attempt}.txt"
                start = time.perf_counter()
                return_code, response_text = run_nc_in_container(
                    container=container,
                    target=config.target,
                    port=config.port,
                    timeout=config.timeout,
                    payload=config.payload,
                    response_file=response_file,
                )
                elapsed_ms = (time.perf_counter() - start) * 1000

                if return_code == 0:
                    if config.expect and config.expect not in response_text:
                        status = "FAIL"
                        error = "expected_text_not_found"
                    else:
                        status = "OK"
                        error = ""
                else:
                    status = "FAIL"
                    error = "nc_failed_or_timeout"

                try:
                    response_rel = response_file.relative_to(lab_dir).as_posix()
                except ValueError:
                    response_rel = str(response_file)

                row = {
                    "timestamp": now_iso(),
                    "scenario": config.scenario,
                    "client": client,
                    "attempt": attempt,
                    "target": config.target,
                    "port": config.port,
                    "status": status,
                    "elapsed_ms": f"{elapsed_ms:.2f}",
                    "error": error,
                    "response_file": response_rel,
                }
                writer.writerow(row)
                f.flush()

                line = f"[{client}][{attempt}/{config.attempts}] {status},{elapsed_ms:.2f},{error},{response_rel}"
                write_log(main_log, line)
                write_log(client_log, line, also_stdout=False)

                if attempt < config.attempts and config.delay > 0:
                    time.sleep(config.delay)

    write_log(main_log, "")
    write_log(main_log, "[OK] Test terminati.")
    write_log(main_log, f"[OK] CSV: {csv_path}")
    write_log(main_log, f"[OK] Log principale: {main_log}")
    write_log(main_log, f"[OK] Risposte complete: {responses_dir}")
    return csv_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Esegue test di connessione via nc da nodi Kathara verso un servizio TCP."
    )
    parser.add_argument("--target", help="IP o hostname del server target, ad esempio 10.0.20.10")
    parser.add_argument("--port", type=int, help="Porta TCP del servizio, ad esempio 9000")
    parser.add_argument("--clients", help="Client Kathara separati da virgola o spazio, es. pc_a,pc_b,pc_c. Se omesso, vengono rilevati automaticamente dai container pc_*. ")
    parser.add_argument("--scenario", default="baseline", help="Nome scenario, es. baseline o dos")
    parser.add_argument("--attempts", type=int, default=10, help="Tentativi per client")
    parser.add_argument("--timeout", type=int, default=3, help="Timeout nc in secondi")
    parser.add_argument("--delay", type=float, default=1.0, help="Pausa tra tentativi")
    parser.add_argument("--payload", default="1\n", help="Payload inviato al servizio via nc")
    parser.add_argument("--expect", default="", help="Testo atteso nella risposta, es. pong")
    parser.add_argument("--non-interactive", action="store_true", help="Non fare domande; richiede target, port e clients")
    return parser.parse_args()


def main() -> int:
    try:
        lab_dir = Path.cwd()
        args = parse_args()
        config = build_config(args, lab_dir)
        csv_path = run_tests(config, lab_dir)
        print("")
        print("Per visualizzare il riepilogo:")
        print(f"  ./show_connection_results.py {shlex.quote(str(csv_path))}")
        return 0
    except KeyboardInterrupt:
        print("\n[INTERRUZIONE] Test interrotto dall'utente.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"[ERRORE] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
