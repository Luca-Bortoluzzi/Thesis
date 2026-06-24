#!/usr/bin/env python3

import argparse
import sys
from pathlib import Path

from zombies_start import DEFAULT_LAB, kathara_connect, project_root


def die(message):
    print(f"[ERRORE] {message}", file=sys.stderr)
    sys.exit(1)


def stop_command():
    return r"""
set -u

if [ -f /zombies_stop.py ]; then
    python3 /zombies_stop.py
elif [ -f /hostlab/attacker/zombies_stop.py ]; then
    python3 /hostlab/attacker/zombies_stop.py
else
    python3 - <<'PY'
import os
import socket
import threading

ZOMBIE_PORT = 9999
ZOMBIES_PATHS = (
    "/hostlab/attacker/zombies.txt",
    "/hostlab/zombies.txt",
    "/zombies.txt",
    "zombies.txt",
)

def find_zombies_file():
    for path in ZOMBIES_PATHS:
        if os.path.exists(path):
            return path
    return None

def send_stop(ip):
    try:
        with socket.create_connection((ip, ZOMBIE_PORT), timeout=4) as sock:
            sock.sendall(b"STOP\n")
            reply = sock.recv(1024).decode(errors="ignore").strip()
        print(f"[OK] Zombie {ip}: stop inviato" + (f" - {reply}" if reply else ""))
    except Exception as exc:
        print(f"[ERRORE] Zombie {ip} non raggiungibile: {exc}")

zombies_file = find_zombies_file()
if not zombies_file:
    print("[ERRORE] zombies.txt non trovato")
    raise SystemExit(1)

with open(zombies_file, "r", encoding="utf-8") as file:
    zombies = [
        line.strip()
        for line in file
        if line.strip() and not line.strip().startswith("#")
    ]

if not zombies:
    print(f"[ERRORE] {zombies_file} non contiene IP zombie")
    raise SystemExit(1)

print(f"[INFO] Invio STOP agli zombie: {', '.join(zombies)}")

threads = []
for ip in zombies:
    thread = threading.Thread(target=send_stop, args=(ip,), daemon=True)
    thread.start()
    threads.append(thread)

for thread in threads:
    thread.join()

print("[OK] Stop completato.")
PY
fi
"""


def main():
    parser = argparse.ArgumentParser(description="Ferma il carico degli zombie Kathara.")
    parser.add_argument("--lab", default=DEFAULT_LAB)
    parser.add_argument("--attacker-node", default="attacker")
    args = parser.parse_args()

    root = project_root()
    lab_dir = root / "labs" / args.lab

    if not lab_dir.exists():
        die(f"Laboratorio non trovato: {lab_dir}")

    rc, output = kathara_connect(lab_dir, args.attacker_node, stop_command())
    print(output)

    if rc != 0:
        sys.exit(rc)


if __name__ == "__main__":
    main()
