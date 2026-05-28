#!/bin/bash
# Auto-generated start script
set -e

echo "[1/2] Avvio laboratorio Kathara..."
kathara lstart

echo "[2/2] Wireshark non abilitato nello YAML."
echo "      Per abilitarlo aggiungi:"
echo "      wireshark:"
echo "        enabled: true"
echo "        interface: any"
