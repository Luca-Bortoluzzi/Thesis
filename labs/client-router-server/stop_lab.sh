#!/bin/bash
# Auto-generated stop script
set -e

echo "[1/1] Arresto laboratorio Kathara..."
kathara lclean

echo "[INFO] Wireshark non viene chiuso automaticamente per evitare perdita di catture non salvate."
echo "       Se vuoi chiuderlo da terminale: pkill wireshark"
