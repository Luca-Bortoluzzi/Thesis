#!/usr/bin/env python3

import argparse
import os
import pty
import re
import select
import shutil
import subprocess
import sys
import time
from pathlib import Path


DEFAULT_LAB = "dos_enterprise_lab"
DEFAULT_SOURCE = Path("attacks/zombie/zombie.py")
DEFAULT_SHARED_SCRIPT = "zombie.py"
TIMEOUT = 12
MARKER = "__ZOMBIES_START_RC__:"


def die(message):
    print(f"[ERRORE] {message}", file=sys.stderr)
    sys.exit(1)


def clean_terminal(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\x1b\][^\x07]*(?:\x07|\x1b\\)", "", text)
    return re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)


def kathara_connect(lab_dir, node, script, timeout=TIMEOUT):
    master, slave = pty.openpty()
    proc = subprocess.Popen(
        ["kathara", "connect", node],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        cwd=lab_dir,
        env={**os.environ, "TERM": os.environ.get("TERM", "xterm")},
    )
    os.close(slave)

    output = bytearray()
    deadline = time.monotonic() + timeout

    try:
        while time.monotonic() < deadline:
            if select.select([master], [], [], 0.2)[0]:
                try:
                    chunk = os.read(master, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                output.extend(chunk)

                if re.search(r"(?m)[#$]\s*$", clean_terminal(output.decode(errors="ignore"))):
                    break

            if proc.poll() is not None:
                break

        wrapped = (
            "sh -s <<'ZOMBIES_START_SCRIPT'\n"
            f"{script.rstrip()}\n"
            "ZOMBIES_START_SCRIPT\n"
            "rc=$?\n"
            "printf '\\n%s%s%s\\n' '__ZOMBIES_START_' 'RC__:' \"$rc\"\n"
            "exit\n"
        )
        os.write(master, wrapped.encode())

        while time.monotonic() < deadline:
            if select.select([master], [], [], 0.2)[0]:
                try:
                    chunk = os.read(master, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                output.extend(chunk)

                if MARKER in clean_terminal(output.decode(errors="ignore")):
                    break

            if proc.poll() is not None:
                break
        else:
            proc.kill()
            return 124, "Timeout durante kathara connect"
    finally:
        os.close(master)

    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            proc.kill()

    text = clean_terminal(output.decode(errors="ignore")).strip()

    if "CRITICAL (" in text:
        return 1, text

    if MARKER not in text:
        return proc.wait(), text

    before, after = text.rsplit(MARKER, 1)
    try:
        return int(after.splitlines()[0].strip()), before.strip()
    except (IndexError, ValueError):
        return 1, before.strip()


def project_root():
    here = Path.cwd()
    candidates = [here, *here.parents]

    for path in candidates:
        if (path / "labs").is_dir():
            return path

    die("Cartella labs/ non trovata. Esegui lo script dalla root del progetto.")


def zombie_nodes(lab_conf):
    if not lab_conf.exists():
        die(f"File lab.conf non trovato: {lab_conf}")

    nodes = set()

    for line in lab_conf.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "[" in line:
            node = line.split("[", 1)[0].strip()
            if node.startswith("zombie"):
                nodes.add(node)

    return sorted(nodes)


def copy_script(root, lab_dir, source, name):
    if Path(name).name != name or not re.fullmatch(r"[A-Za-z0-9._-]+", name):
        die("--shared-script deve essere solo un nome file semplice, ad esempio zombie.py")

    source_path = root / source
    if not source_path.exists():
        die(f"Script zombie sorgente non trovato: {source_path}")

    shared_dir = lab_dir / "shared"
    shared_dir.mkdir(parents=True, exist_ok=True)
    destination = shared_dir / name
    shutil.copy2(source_path, destination)
    return destination


def remote_script(shared_script, restart=False):
    log_name = f"{Path(shared_script).stem}.log"
    restart_value = "1" if restart else "0"

    return f"""
set -u
SCRIPT_NAME={shared_script!r}
LOG_NAME={log_name!r}
RESTART={restart_value!r}

find_pids() {{
    for proc in /proc/[0-9]*; do
        pid="${{proc##*/}}"
        cmd="$(tr '\\0' ' ' < "$proc/cmdline" 2>/dev/null || true)"
        case "$cmd" in
            *python3*"/$SCRIPT_NAME"*) echo "$pid" ;;
        esac
    done
}}

if [ "$RESTART" = "1" ]; then
    for pid in $(find_pids); do kill -9 "$pid" 2>/dev/null || true; done
fi

pids="$(find_pids | paste -sd, -)"
if [ -n "$pids" ]; then
    echo "RUNNING|$pids"
    exit 0
fi

for base in /shared /hostlab/shared /hostlab; do
    if [ -f "$base/$SCRIPT_NAME" ]; then
        SCRIPT="$base/$SCRIPT_NAME"
        LOG="$base/$LOG_NAME"
        break
    fi
done

if [ -z "${{SCRIPT:-}}" ]; then
    echo "FAILED|Script non trovato: $SCRIPT_NAME"
    exit 1
fi

chmod +x "$SCRIPT" 2>/dev/null || true
nohup python3 -u "$SCRIPT" > "$LOG" 2>&1 < /dev/null &
sleep 1

pids="$(find_pids | paste -sd, -)"
if [ -n "$pids" ]; then
    echo "STARTED|$pids"
else
    echo "FAILED|$(tail -n 20 "$LOG" 2>/dev/null || echo 'script terminato senza log')"
    exit 1
fi
"""


def main():
    parser = argparse.ArgumentParser(description="Avvia zombie.py nei nodi zombie Kathara.")
    parser.add_argument("--lab", default=DEFAULT_LAB)
    parser.add_argument("--source", default=str(DEFAULT_SOURCE))
    parser.add_argument("--shared-script", default=DEFAULT_SHARED_SCRIPT)
    parser.add_argument("--nodes", help="Lista nodi separati da virgola, es. zombie_1,zombie_2")
    parser.add_argument("--restart", action="store_true")
    args = parser.parse_args()

    root = project_root()
    lab_dir = root / "labs" / args.lab
    if not lab_dir.exists():
        die(f"Laboratorio non trovato: {lab_dir}")

    nodes = [n.strip() for n in args.nodes.split(",") if n.strip()] if args.nodes else zombie_nodes(lab_dir / "lab.conf")
    if not nodes:
        die("Nessun nodo zombie trovato. Verifica lab.conf oppure usa --nodes.")

    copied = copy_script(root, lab_dir, Path(args.source), args.shared_script)
    print(f"[OK] Script copiato in: {copied}")
    print(f"[INFO] Nodi zombie rilevati: {', '.join(nodes)}")
    print("\n=== RISULTATO ===")

    failed = False

    for node in nodes:
        rc, output = kathara_connect(lab_dir, node, remote_script(args.shared_script, args.restart))
        status_line = next(
            (
                line
                for line in output.splitlines()
                if line.startswith(("STARTED|", "RUNNING|", "FAILED|"))
            ),
            ""
        )
        status, _, detail = status_line.partition("|")

        if rc == 0 and status == "STARTED":
            print(f"[AVVIATO] {node} | PID: {detail}")
        elif rc == 0 and status == "RUNNING":
            print(f"[GIÀ ATTIVO] {node} | PID: {detail}")
        else:
            failed = True
            print(f"[FALLITO] {node} -> {detail or output or 'errore sconosciuto'}")

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
