#!/usr/bin/env python3
"""
zombies_stop.py - ferma il carico sugli zombie del laboratorio Kathara.

Uso nel container attacker:
  python3 /hostlab/attacker/zombies_stop.py
"""
import os
import socket
import sys
import threading


ZOMBIE_PORT = 9999
ZOMBIES_PATHS = ("/hostlab/attacker/zombies.txt", "/hostlab/zombies.txt", "/zombies.txt", "zombies.txt")


def find_zombies_file() -> str | None:
    for path in ZOMBIES_PATHS:
        if os.path.exists(path):
            return path
    return None


def read_zombies(path: str) -> list[str]:
    with open(path, "r", encoding="utf-8") as file:
        return [
            line.strip()
            for line in file
            if line.strip() and not line.strip().startswith("#")
        ]


def send_stop(zombie_ip: str) -> None:
    try:
        with socket.create_connection((zombie_ip, ZOMBIE_PORT), timeout=4) as sock:
            sock.sendall(b"STOP\n")
            try:
                reply = sock.recv(1024).decode(errors="ignore").strip()
            except OSError:
                reply = ""
        print(f"[OK] Zombie {zombie_ip}: stop inviato" + (f" - {reply}" if reply else ""))
    except Exception as exc:
        print(f"[ERRORE] Zombie {zombie_ip} non raggiungibile su porta {ZOMBIE_PORT}: {exc}")


def main() -> int:
    zombies_file = find_zombies_file()
    if not zombies_file:
        print(f"[ERRORE] zombies.txt non trovato. Percorsi provati: {', '.join(ZOMBIES_PATHS)}")
        return 1

    zombies = read_zombies(zombies_file)
    if not zombies:
        print(f"[ERRORE] {zombies_file} esiste ma non contiene IP zombie.")
        return 1

    print(f"[INFO] Invio STOP agli zombie: {', '.join(zombies)}")

    threads = []
    for zombie_ip in zombies:
        thread = threading.Thread(target=send_stop, args=(zombie_ip,), daemon=True)
        thread.start()
        threads.append(thread)

    for thread in threads:
        thread.join()

    print("[OK] Stop completato.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
