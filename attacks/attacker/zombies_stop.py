#!/usr/bin/env python3
"""
zombies_stop.py - stops the load on Kathara lab zombies.

Usage inside the attacker container:
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
        print(f"[OK] Zombie {zombie_ip}: stop sent" + (f" - {reply}" if reply else ""))
    except Exception as exc:
        print(f"[ERROR] Zombie {zombie_ip} is unreachable on port {ZOMBIE_PORT}: {exc}")


def main() -> int:
    zombies_file = find_zombies_file()
    if not zombies_file:
        print(f"[ERROR] zombies.txt not found. Paths checked: {', '.join(ZOMBIES_PATHS)}")
        return 1

    zombies = read_zombies(zombies_file)
    if not zombies:
        print(f"[ERROR] {zombies_file} exists but contains no zombie IP addresses.")
        return 1

    print(f"[INFO] Sending STOP to zombies: {', '.join(zombies)}")

    threads = []
    for zombie_ip in zombies:
        thread = threading.Thread(target=send_stop, args=(zombie_ip,), daemon=True)
        thread.start()
        threads.append(thread)

    for thread in threads:
        thread.join()

    print("[OK] Stop complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
