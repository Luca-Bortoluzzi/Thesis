#!/usr/bin/env python3
"""Rate-limiting TCP reverse proxy for the isolated Kathara mitigation lab."""

from __future__ import annotations

import os
import select
import socket
import threading
import time


LISTEN_HOST = os.getenv("PROXY_LISTEN_HOST", "0.0.0.0")
LISTEN_PORT = int(os.getenv("PROXY_LISTEN_PORT", "9000"))
BACKEND_HOST = os.getenv("PROXY_BACKEND_HOST", "10.30.2.10")
BACKEND_PORT = int(os.getenv("PROXY_BACKEND_PORT", "9000"))
BACKLOG = int(os.getenv("PROXY_BACKLOG", "64"))
MAX_ACTIVE = int(os.getenv("PROXY_MAX_ACTIVE", "32"))
MAX_PER_SOURCE = int(os.getenv("PROXY_MAX_PER_SOURCE", "2"))
CONNECT_TIMEOUT = float(os.getenv("PROXY_CONNECT_TIMEOUT", "2"))
IDLE_TIMEOUT = float(os.getenv("PROXY_IDLE_TIMEOUT", "12"))

global_slots = threading.BoundedSemaphore(MAX_ACTIVE)
source_lock = threading.Lock()
active_by_source: dict[str, int] = {}


def safe_send(sock: socket.socket, payload: bytes) -> None:
    try:
        sock.sendall(payload)
    except OSError:
        pass


def reserve_source(source_ip: str) -> tuple[bool, str]:
    """Reserve global and per-source capacity for an accepted connection."""
    if not global_slots.acquire(blocking=False):
        return False, "global_capacity"

    with source_lock:
        active = active_by_source.get(source_ip, 0)
        if active >= MAX_PER_SOURCE:
            global_slots.release()
            return False, "source_limit"
        active_by_source[source_ip] = active + 1
    return True, ""


def release_source(source_ip: str) -> None:
    with source_lock:
        remaining = active_by_source.get(source_ip, 1) - 1
        if remaining > 0:
            active_by_source[source_ip] = remaining
        else:
            active_by_source.pop(source_ip, None)
    global_slots.release()


def relay(client: socket.socket, backend: socket.socket) -> None:
    """Relay bytes in both directions until either side closes or becomes idle."""
    last_activity = time.monotonic()
    sockets = (client, backend)
    while True:
        if time.monotonic() - last_activity >= IDLE_TIMEOUT:
            return
        readable, _, exceptional = select.select(sockets, [], sockets, 1.0)
        if exceptional:
            return
        for source in readable:
            try:
                payload = source.recv(65536)
            except OSError:
                return
            if not payload:
                return
            destination = backend if source is client else client
            try:
                destination.sendall(payload)
            except OSError:
                return
            last_activity = time.monotonic()


def handle_client(client: socket.socket, address: tuple[str, int]) -> None:
    source_ip = address[0]
    with source_lock:
        source_active = active_by_source[source_ip]
    print(
        f"[ACCEPT] {address}; source_active={source_active}/{MAX_PER_SOURCE}",
        flush=True,
    )

    try:
        client.settimeout(None)
        with socket.create_connection(
            (BACKEND_HOST, BACKEND_PORT), timeout=CONNECT_TIMEOUT
        ) as backend:
            backend.settimeout(None)
            relay(client, backend)
    except OSError as exc:
        print(f"[BACKEND] Connection for {address} failed: {exc}", flush=True)
        safe_send(client, b"BACKEND_UNAVAILABLE\n")
    finally:
        try:
            client.close()
        except OSError:
            pass
        release_source(source_ip)
        print(f"[CLOSE] {address}", flush=True)


def main() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((LISTEN_HOST, LISTEN_PORT))
        listener.listen(BACKLOG)
        print(
            f"[OK] Reverse proxy listening on {LISTEN_HOST}:{LISTEN_PORT}; "
            f"backend={BACKEND_HOST}:{BACKEND_PORT}; max_active={MAX_ACTIVE}; "
            f"max_per_source={MAX_PER_SOURCE}",
            flush=True,
        )
        while True:
            client, address = listener.accept()
            reserved, reason = reserve_source(address[0])
            if not reserved:
                print(f"[LIMIT] Rejected {address}: {reason}", flush=True)
                safe_send(client, b"RATE_LIMITED\n")
                client.close()
                continue
            threading.Thread(
                target=handle_client,
                args=(client, address),
                daemon=True,
            ).start()


if __name__ == "__main__":
    main()
