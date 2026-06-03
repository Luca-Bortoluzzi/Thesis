#!/bin/bash
LAB_NAME='Test1'
CONTAINER="wireshark-sniffer-$LAB_NAME"

if ! docker ps --format "{{.Names}}" | grep -qx "$CONTAINER"; then
  echo "[ERRORE] Container sniffer non attivo: $CONTAINER"
  echo "Avvia prima Wireshark con:"
  echo "  ./start_wireshark.sh [any|eth0|eth1|...]"
  echo ""
  echo "Container Wireshark disponibili:"
  docker ps --format "{{.Names}}" | grep '^wireshark-' || true
  exit 1
fi

echo "[OK] Seguo i log dello sniffer: $CONTAINER"
echo "[INFO] Premi CTRL+C per uscire dai log. Lo sniffing continuerà in background."
echo ""
exec docker logs -f "$CONTAINER"
