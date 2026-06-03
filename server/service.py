#!/usr/bin/env python3

import socket

HOST = "0.0.0.0"
PORT = 8080

def handle_client(conn, addr):
    conn.sendall(b"=================================\n")
    conn.sendall(b" Benvenuto nel servizio Test1\n")
    conn.sendall(b"=================================\n\n")
    conn.sendall(b"1) Ping\n")
    conn.sendall(b"2) Info server\n")
    conn.sendall(b"3) Esci\n\n")
    conn.sendall(b"Scelta: ")

    data = conn.recv(1024).decode(errors="ignore").strip()

    if data == "1":
        conn.sendall(b"\npong\n")
    elif data == "2":
        conn.sendall(b"\nServer DMZ - IP 10.0.20.10 - Porta 8080\n")
    elif data == "3":
        conn.sendall(b"\nChiusura connessione\n")
    else:
        conn.sendall(b"\nScelta non valida\n")

    conn.close()

def main():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((HOST, PORT))
        server.listen(5)

        print(f"[+] Servizio Python in ascolto su {HOST}:{PORT}")

        while True:
            conn, addr = server.accept()
            handle_client(conn, addr)

if __name__ == "__main__":
    main()