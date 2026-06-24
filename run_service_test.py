#!/usr/bin/env python3
"""
run_acceptance_tests.py

Esegue test di accettazione TCP dai client Kathara verso un servizio.

A differenza di run_connection_tests.py, questo script NON verifica la risposta
applicativa 'pong'. Misura solo se la connessione TCP viene accettata,
rifiutata, resettata o va in timeout.

Serve per distinguere:
- problema applicativo: TCP accettato ma niente risposta corretta;
- problema backlog/kernel: TCP refused/reset/timeout.
"""

from __future__ import annotations

import argparse
import csv
import ipaddress
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
        raise RuntimeError(f"Impossibile leggere i container Docker: {result.stderr.strip()}")

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

    labs = [
        path
        for path in labs_dir.iterdir()
        if path.is_dir() and (path / "lab.conf").exists()
    ]

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
        print(f"[INFO] Uso laboratorio rilevato: {labs[0].name}")
        return labs[0]

    print("Laboratori disponibili in ./labs:")

    for index, lab in enumerate(labs, start=1):
        print(f"  {index}) {lab.name}")

    while True:
        raw = input(f"Scegli laboratorio [1-{len(labs)}]: ").strip()

        try:
            choice = int(raw)
        except ValueError:
            print("Scelta non valida.")
            continue

        if 1 <= choice <= len(labs):
            return labs[choice - 1]

        print("Scelta fuori intervallo.")


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
            raise ValueError(f"Laboratorio non trovato o non valido: {candidate}")

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

    raise ValueError("Sono presenti piu' laboratori in ./labs: usa --lab <nome_lab>.")


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
            print(f"[INFO] Target '{target}' risolto da {startup_path}: {ip_addr}")
            return ip_addr

        raise ValueError(
            f"Target '{target}' non risolto. File non trovato o senza IP: {startup_path}. "
            "Usa un IP oppure seleziona il laboratorio corretto con --lab."
        )

    raise ValueError(
        f"Target '{target}' non e' un IP e non e' stato possibile determinare il laboratorio. "
        "Esegui lo script dentro labs/<nome_lab>/ oppure usa --lab <nome_lab>."
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


def run_tcp_acceptance_test(container: str, target: str, port: int, timeout: int) -> tuple[int, str]:
    """
    Testa solo l'accettazione TCP.

    Non invia '1\\n' e non cerca 'pong'.
    Classifica:
    - TCP_ACCEPTED
    - TCP_REFUSED
    - TCP_TIMEOUT
    - TCP_RESET
    - TCP_ERROR
    """

    python_code = r'''
import socket
import time
import os
import sys

target = os.environ["TARGET"]
port = int(os.environ["PORT"])
timeout = int(os.environ["TIMEOUT"])

sock = None
start = time.perf_counter()

try:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    sock.connect((target, port))

    elapsed_ms = (time.perf_counter() - start) * 1000
    print(f"TCP_ACCEPTED|{elapsed_ms:.2f}|")
    sys.exit(0)

except socket.timeout as exc:
    elapsed_ms = (time.perf_counter() - start) * 1000
    print(f"TCP_TIMEOUT|{elapsed_ms:.2f}|{exc}")
    sys.exit(2)

except ConnectionRefusedError as exc:
    elapsed_ms = (time.perf_counter() - start) * 1000
    print(f"TCP_REFUSED|{elapsed_ms:.2f}|{exc}")
    sys.exit(3)

except ConnectionResetError as exc:
    elapsed_ms = (time.perf_counter() - start) * 1000
    print(f"TCP_RESET|{elapsed_ms:.2f}|{exc}")
    sys.exit(4)

except OSError as exc:
    elapsed_ms = (time.perf_counter() - start) * 1000
    print(f"TCP_ERROR|{elapsed_ms:.2f}|{exc}")
    sys.exit(5)

finally:
    if sock is not None:
        try:
            sock.close()
        except OSError:
            pass
'''.strip()

    command = [
        "docker", "exec",
        "-e", f"TARGET={target}",
        "-e", f"PORT={port}",
        "-e", f"TIMEOUT={timeout}",
        container,
        "python3",
        "-c",
        python_code,
    ]

    completed = subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )

    return completed.returncode, completed.stdout or ""


def parse_acceptance_output(return_code: int, output: str) -> tuple[str, str, str]:
    """
    Ritorna:
    - status
    - connect_time_ms
    - error
    """

    output = (output or "").strip()

    if not output:
        return "EXEC_ERROR", "", "nessun output dal comando docker exec"

    last_line = output.splitlines()[-1].strip()
    parts = last_line.split("|", 2)

    if len(parts) < 2:
        return "EXEC_ERROR", "", output

    status = parts[0].strip()
    connect_time_ms = parts[1].strip()
    error = parts[2].strip() if len(parts) >= 3 else ""

    valid_statuses = {
        "TCP_ACCEPTED",
        "TCP_REFUSED",
        "TCP_TIMEOUT",
        "TCP_RESET",
        "TCP_ERROR",
    }

    if status not in valid_statuses:
        return "EXEC_ERROR", "", output

    return status, connect_time_ms, error


def run_one_attempt(client: str, container: str, attempt: int, config: TestConfig) -> dict[str, str]:
    return_code, output = run_tcp_acceptance_test(
        container=container,
        target=config.target_resolved,
        port=config.port,
        timeout=config.timeout,
    )

    status, connect_time_ms, error = parse_acceptance_output(return_code, output)

    if status == "TCP_ACCEPTED":
        result = "ACCEPTED"
    else:
        result = "FAILED"

    return {
        "timestamp": now_iso(),
        "scenario": config.scenario,
        "client": client,
        "attempt": str(attempt),
        "packet_number": str(attempt),
        "target_input": config.target_input,
        "target": config.target_resolved,
        "port": str(config.port),
        "result": result,
        "status": status,
        "connect_time_ms": connect_time_ms,
        "error": error,
        "debug_output": output.strip(),
    }


def build_config(args: argparse.Namespace, context: LabContext) -> TestConfig:
    interactive = not args.non_interactive

    target_input = args.target
    port_value = str(args.port) if args.port is not None else ""
    clients_value = args.clients or ""
    scenario = args.scenario or "acceptance"
    attempts_value = str(args.attempts) if args.attempts is not None else "10"
    timeout_value = str(args.timeout) if args.timeout is not None else "3"
    delay_value = str(args.delay) if args.delay is not None else "1"

    if interactive:
        print("Configurazione test accettazione TCP")
        print("Il test verifica se i client riescono ad aprire una connessione TCP.")
        print("Non viene verificata la risposta applicativa del servizio.")
        print("Per ogni tentativo, tutti i client vengono eseguiti simultaneamente.")
        print("Premi INVIO per accettare il valore predefinito, quando presente.\n")

        if context.lab_dir:
            print(f"[INFO] Laboratorio selezionato: {context.lab_dir.name}")

        target_input = prompt_value("IP/host server target", target_input, required=True)
        port_value = prompt_value("Porta TCP servizio", port_value or "9000", required=True)

        clients_default = clients_value or default_clients_from_lab(context.lab_dir)

        if clients_default:
            print(f"Client rilevati automaticamente: {clients_default}")

        clients_value = prompt_value("Client Kathara da usare", clients_default, required=True)

        scenario = prompt_value("Scenario", scenario or "acceptance", required=True)
        attempts_value = prompt_value("Tentativi simultanei per client", attempts_value or "10", required=True)
        timeout_value = prompt_value("Timeout secondi", timeout_value or "3", required=True)
        delay_value = prompt_value("Delay tra round simultanei", delay_value or "1", required=True)

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
            raise ValueError("In modalita' --non-interactive mancano: " + ", ".join(missing))

    if target_input is None or not target_input.strip():
        raise ValueError("Target mancante.")

    target_resolved = resolve_target(target_input, context.lab_dir)

    return TestConfig(
        target_input=target_input.strip(),
        target_resolved=target_resolved,
        port=parse_int(port_value, "Porta", minimum=1),
        clients=parse_clients(clients_value),
        scenario=scenario.strip() or "acceptance",
        attempts=parse_int(attempts_value, "Tentativi", minimum=1),
        timeout=parse_int(timeout_value, "Timeout", minimum=1),
        delay=parse_float(delay_value, "Delay", minimum=0.0),
        lab_dir=context.lab_dir,
    )


def summarize_rows(rows: list[dict[str, str]]) -> str:
    total = len(rows)

    if total == 0:
        return "Nessun risultato."

    counters = {
        "TCP_ACCEPTED": 0,
        "TCP_REFUSED": 0,
        "TCP_TIMEOUT": 0,
        "TCP_RESET": 0,
        "TCP_ERROR": 0,
        "EXEC_ERROR": 0,
    }

    times: list[float] = []

    for row in rows:
        status = row.get("status", "")

        if status in counters:
            counters[status] += 1
        else:
            counters["EXEC_ERROR"] += 1

        if status == "TCP_ACCEPTED":
            try:
                times.append(float(row.get("connect_time_ms", "")))
            except ValueError:
                pass

    accepted = counters["TCP_ACCEPTED"]
    failed = total - accepted

    lines = []
    lines.append("=== RIEPILOGO ACCETTAZIONE TCP ===")
    lines.append(f"Totale tentativi:       {total}")
    lines.append(f"TCP accettate:          {accepted}")
    lines.append(f"TCP rifiutate:          {counters['TCP_REFUSED']}")
    lines.append(f"TCP timeout:            {counters['TCP_TIMEOUT']}")
    lines.append(f"TCP reset:              {counters['TCP_RESET']}")
    lines.append(f"TCP error:              {counters['TCP_ERROR']}")
    lines.append(f"Errori esecuzione:      {counters['EXEC_ERROR']}")
    lines.append(f"Tasso accettazione:     {(accepted / total) * 100:.2f}%")
    lines.append(f"Tasso fallimento TCP:   {(failed / total) * 100:.2f}%")

    if times:
        lines.append("")
        lines.append("Tempi di connessione per TCP_ACCEPTED:")
        lines.append(f"Media:                  {sum(times) / len(times):.2f} ms")
        lines.append(f"Min:                    {min(times):.2f} ms")
        lines.append(f"Max:                    {max(times):.2f} ms")

    return "\n".join(lines)


def run_tests(config: TestConfig, output_dir: Path) -> Path:
    run_id = now_run_id()

    results_dir = output_dir / "results"
    logs_dir = output_dir / "logs"

    results_dir.mkdir(exist_ok=True)
    logs_dir.mkdir(exist_ok=True)

    csv_path = results_dir / f"acceptance_results_{config.scenario}_{run_id}.csv"
    main_log = logs_dir / f"acceptance_tests_{config.scenario}_{run_id}.log"

    write_log(main_log, f"[INFO] Output dir: {output_dir}")

    if config.lab_dir:
        write_log(main_log, f"[INFO] Lab selezionato: {config.lab_dir}")

    write_log(main_log, f"[INFO] Scenario: {config.scenario}")
    write_log(main_log, f"[INFO] Target input: {config.target_input}")
    write_log(main_log, f"[INFO] Target risolto: {config.target_resolved}:{config.port}")
    write_log(main_log, f"[INFO] Client: {' '.join(config.clients)}")
    write_log(main_log, f"[INFO] Tentativi simultanei per client: {config.attempts}")
    write_log(main_log, f"[INFO] Timeout: {config.timeout}s")
    write_log(main_log, f"[INFO] Delay tra round: {config.delay}s")
    write_log(main_log, f"[INFO] Output CSV: {csv_path}")
    write_log(main_log, "")

    containers = list_docker_containers()
    client_to_container: dict[str, str] = {}

    for client in config.clients:
        container = find_kathara_container(client, containers)
        client_log = logs_dir / f"{client}_{config.scenario}_{run_id}.log"

        if not container:
            message = f"[ERRORE] Container non trovato per nodo: {client}"
            write_log(main_log, message)
            write_log(client_log, message, also_stdout=False)
            continue

        client_to_container[client] = container
        write_log(main_log, f"[OK] Nodo {client} -> container {container}")
        write_log(client_log, f"[OK] Nodo {client} -> container {container}", also_stdout=False)

    if not client_to_container:
        raise ValueError("Nessun container client valido trovato.")

    fieldnames = [
        "timestamp",
        "scenario",
        "client",
        "attempt",
        "packet_number",
        "target_input",
        "target",
        "port",
        "result",
        "status",
        "connect_time_ms",
        "error",
    ]

    all_rows: list[dict[str, str]] = []

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for attempt in range(1, config.attempts + 1):
            write_log(main_log, f"[ROUND {attempt}/{config.attempts}] Avvio test accettazione simultanei")

            rows: list[dict[str, str]] = []

            with ThreadPoolExecutor(max_workers=len(client_to_container)) as executor:
                future_map = {
                    executor.submit(run_one_attempt, client, container, attempt, config): client
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
                            "scenario": config.scenario,
                            "client": client,
                            "attempt": str(attempt),
                            "packet_number": str(attempt),
                            "target_input": config.target_input,
                            "target": config.target_resolved,
                            "port": str(config.port),
                            "result": "FAILED",
                            "status": "EXEC_ERROR",
                            "connect_time_ms": "",
                            "error": f"internal_error:{exc}",
                            "debug_output": "",
                        }

                    rows.append(row)

                    line = (
                        f"[{client}][{attempt}/{config.attempts}] "
                        f"{row['status']},"
                        f"connect_time_ms={row['connect_time_ms']},"
                        f"error={row['error']}"
                    )

                    write_log(main_log, line)
                    write_log(client_log, line, also_stdout=False)

                    if row.get("debug_output"):
                        write_log(
                            client_log,
                            f"[DEBUG] output: {row['debug_output']}",
                            also_stdout=False,
                        )

            for row in sorted(rows, key=lambda item: item["client"]):
                row_for_csv = {key: row.get(key, "") for key in fieldnames}
                writer.writerow(row_for_csv)
                all_rows.append(row)

            f.flush()

            if attempt < config.attempts and config.delay > 0:
                time.sleep(config.delay)

    write_log(main_log, "")
    write_log(main_log, summarize_rows(all_rows))
    write_log(main_log, "")
    write_log(main_log, "[OK] Test accettazione terminati.")
    write_log(main_log, f"[OK] CSV: {csv_path}")
    write_log(main_log, f"[OK] Log principale: {main_log}")

    print("")
    print(summarize_rows(all_rows))

    return csv_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verifica accettazione TCP simultanea dai nodi Kathara verso un servizio."
    )

    parser.add_argument("--lab", help="Nome laboratorio in ./labs oppure percorso del laboratorio")
    parser.add_argument("--target", help="IP o nome nodo del server target, ad esempio 10.0.20.10 oppure server")
    parser.add_argument("--port", type=int, help="Porta TCP del servizio, ad esempio 9000")

    parser.add_argument(
        "--clients",
        help="Client Kathara separati da virgola o spazio. Se omesso, rileva automaticamente pc_*.",
    )

    parser.add_argument("--scenario", default="acceptance", help="Nome scenario, es. acceptance_baseline o acceptance_attack")
    parser.add_argument("--attempts", type=int, default=10, help="Numero di round simultanei")
    parser.add_argument("--timeout", type=int, default=3, help="Timeout connect TCP in secondi")
    parser.add_argument("--delay", type=float, default=1.0, help="Pausa tra round simultanei")
    parser.add_argument("--non-interactive", action="store_true", help="Non fare domande; richiede almeno target e port")

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
        print("CSV generato:")
        print(f"  {csv_path}")

        print("")
        print("Esempio comando:")
        print(f"  ./show_acceptance_results.py {shlex.quote(str(csv_path))}")

        return 0

    except KeyboardInterrupt:
        print("\n[INTERRUZIONE] Test interrotto dall'utente.", file=sys.stderr)
        return 130

    except Exception as exc:
        print(f"[ERRORE] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())