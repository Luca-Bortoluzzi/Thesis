#!/usr/bin/env python3
"""
Educational TCP service for availability testing.
It exposes measurable saturation effects through:
- a maximum number of concurrent application handlers;
- a testable 'pong' response;
- overload logging.
"""
import os
import socket
import threading
import traceback

HOST = "0.0.0.0"
PORT = int(os.getenv("SERVICE_PORT", "9000"))
BACKLOG = int(os.getenv("SERVICE_BACKLOG", "40"))
MAX_ACTIVE_CLIENTS = int(os.getenv("SERVICE_MAX_ACTIVE", "20"))
CLIENT_TIMEOUT = int(os.getenv("SERVICE_CLIENT_TIMEOUT", "10"))

slots = threading.BoundedSemaphore(MAX_ACTIVE_CLIENTS)
active_lock = threading.Lock()
active_clients = 0


def safe_send(conn, data: bytes) -> bool:
    try:
        conn.sendall(data)
        return True
    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
        return False


def handle_client(conn, addr):
    global active_clients
    acquired = slots.acquire(blocking=False)
    if not acquired:
        print(f"[OVERLOAD] Connection rejected from {addr}: application limit reached", flush=True)
        safe_send(conn, b"BUSY\n")
        conn.close()
        return

    with active_lock:
        active_clients += 1
        current = active_clients
    print(f"[CONN] {addr} active. Active={current}/{MAX_ACTIVE_CLIENTS}", flush=True)

    try:
        conn.settimeout(CLIENT_TIMEOUT)
        menu = (
            b"=================================\n"
            b" DoS Lab TCP Service\n"
            b"=================================\n\n"
            b"1) Ping\n"
            b"2) Server information\n"
            b"3) Exit\n\n"
            b"Selection: "
        )
        if not safe_send(conn, menu):
            return

        try:
            raw = conn.recv(1024)
        except socket.timeout:
            safe_send(conn, b"\nTimeout: no selection received\n")
            return
        except (ConnectionResetError, ConnectionAbortedError):
            return

        if not raw:
            return

        data = raw.decode(errors="ignore").strip()
        if data == "1":
            safe_send(conn, b"\npong\n")
        elif data == "2":
            safe_send(conn, b"\nDMZ server - TCP service active\n")
        elif data == "3":
            safe_send(conn, b"\nClosing connection\n")
        else:
            safe_send(conn, b"\nInvalid selection\n")

    except Exception:
        print(f"[ERROR] Unexpected error with client {addr}", flush=True)
        traceback.print_exc()
    finally:
        try:
            conn.close()
        except OSError:
            pass
        with active_lock:
            active_clients -= 1
            current = active_clients
        slots.release()
        print(f"[DISC] {addr} closed. Active={current}/{MAX_ACTIVE_CLIENTS}", flush=True)


def main():
    while True:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
                server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                server.bind((HOST, PORT))
                server.listen(BACKLOG)
                print(f"[OK] TCP service active on {HOST}:{PORT}; backlog={BACKLOG}; max_active={MAX_ACTIVE_CLIENTS}", flush=True)
                while True:
                    try:
                        conn, addr = server.accept()
                    except OSError as exc:
                        print(f"[ERROR] accept(): {exc}", flush=True)
                        continue
                    threading.Thread(target=handle_client, args=(conn, addr), daemon=True).start()
        except OSError as exc:
            print(f"[ERROR] server socket: {exc}; restarting", flush=True)


if __name__ == "__main__":
    main()
