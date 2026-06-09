#!/usr/bin/env python3
"""
run_connection_tests.py

Esegue test di raggiungibilita' TCP dai client Kathara verso un servizio.
I client vengono eseguiti in parallelo per ogni tentativo, cosi' la simulazione
riproduce piu' host legittimi che provano a connettersi simultaneamente.

Uso interattivo:
    ./run_connection_tests.py

Uso non interattivo:
    ./run_connection_tests.py --lab Test1 --target server --port 9000 \
        --scenario baseline --attempts 10 --timeout 2 --delay 1 --non-interactive

Output:
- results/connection_results_<scenario>_<timestamp>.csv
- logs/connection_tests_<scenario>_<timestamp>.log
- logs/<client>_<scenario>_<timestamp>.log
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


def run_tcp_connect_test(container: str, target: str, port: int, timeout: int) -> tuple[int, str]:
    """
    Apre una connessione TCP verso il servizio e invia '3\n' per chiudere
    correttamente il servizio interattivo. Il contenuto della risposta non viene
    analizzato: conta solo il ritorno del comando.
    """
    shell_script = r'''
printf '3\n' | timeout "$TIMEOUT" nc -w "$TIMEOUT" "$TARGET" "$PORT" >/dev/null
'''.strip()

    command = [
        "docker", "exec",
        "-e", f"TARGET={target}",
        "-e", f"PORT={port}",
        "-e", f"TIMEOUT={timeout}",
        container,
        "sh", "-lc", shell_script,
    ]
    completed = subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    return completed.returncode, completed.stdout or ""


def run_one_attempt(client: str, container: str, attempt: int, config: TestConfig) -> dict[str, str]:
    start = time.perf_counter()
    return_code, output = run_tcp_connect_test(
        container=container,
        target=config.target_resolved,
        port=config.port,
        timeout=config.timeout,
    )
    elapsed_ms = (time.perf_counter() - start) * 1000

    status = "OK" if return_code == 0 else "FAIL"
    error = "" if return_code == 0 else "tcp_connection_failed_or_timeout"

    return {
        "timestamp": now_iso(),
        "scenario": config.scenario,
        "client": client,
        "attempt": str(attempt),
        "packet_number": str(attempt),
        "target_input": config.target_input,
        "target": config.target_resolved,
        "port": str(config.port),
        "status": status,
        "elapsed_ms": f"{elapsed_ms:.2f}",
        "error": error,
        "response_file": "",
        "debug_output": output.strip(),
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
        print("Configurazione test connessioni TCP")
        print("Il test verifica solo se i client riescono a connettersi al server.")
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

        scenario = prompt_value("Scenario", scenario or "baseline", required=True)
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
        scenario=scenario.strip() or "baseline",
        attempts=parse_int(attempts_value, "Tentativi", minimum=1),
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
        "timestamp", "scenario", "client", "attempt", "packet_number",
        "target_input", "target", "port", "status", "elapsed_ms", "error",
        "response_file",
    ]

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for attempt in range(1, config.attempts + 1):
            write_log(main_log, f"[ROUND {attempt}/{config.attempts}] Avvio connessioni simultanee")
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
                            "status": "FAIL",
                            "elapsed_ms": "0.00",
                            "error": f"internal_error:{exc}",
                            "response_file": "",
                            "debug_output": "",
                        }
                    rows.append(row)
                    line = f"[{client}][{attempt}/{config.attempts}] {row['status']},{row['elapsed_ms']},{row['error']}"
                    write_log(main_log, line)
                    write_log(client_log, line, also_stdout=False)
                    if row.get("debug_output"):
                        write_log(client_log, f"[DEBUG] nc output: {row['debug_output']}", also_stdout=False)

            for row in sorted(rows, key=lambda item: item["client"]):
                row_for_csv = {key: row.get(key, "") for key in fieldnames}
                writer.writerow(row_for_csv)
            f.flush()

            if attempt < config.attempts and config.delay > 0:
                time.sleep(config.delay)

    write_log(main_log, "")
    write_log(main_log, "[OK] Test terminati.")
    write_log(main_log, f"[OK] CSV: {csv_path}")
    write_log(main_log, f"[OK] Log principale: {main_log}")
    return csv_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verifica connessioni TCP simultanee dai nodi Kathara verso un servizio."
    )
    parser.add_argument("--lab", help="Nome laboratorio in ./labs oppure percorso del laboratorio")
    parser.add_argument("--target", help="IP o nome nodo del server target, ad esempio 10.0.20.10 oppure server")
    parser.add_argument("--port", type=int, help="Porta TCP del servizio, ad esempio 9000")
    parser.add_argument(
        "--clients",
        help="Client Kathara separati da virgola o spazio. Se omesso, rileva automaticamente pc_*.",
    )
    parser.add_argument("--scenario", default="baseline", help="Nome scenario, es. baseline o dos")
    parser.add_argument("--attempts", type=int, default=10, help="Numero di round simultanei")
    parser.add_argument("--timeout", type=int, default=3, help="Timeout nc in secondi")
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
        print("Per visualizzare il riepilogo:")
        print(f"  ./show_connection_results.py {shlex.quote(str(csv_path))}")
        print("Per generare anche il grafico RTT:")
        print(f"  ./show_connection_results.py {shlex.quote(str(csv_path))} --plot")
        return 0
    except KeyboardInterrupt:
        print("\n[INTERRUZIONE] Test interrotto dall'utente.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"[ERRORE] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
