#!/bin/bash
set -e

IMAGE="lscr.io/linuxserver/wireshark:latest"
IFACE="${1:-any}"

LAB_DIR="$(pwd)"
CAPTURE_DIR="$LAB_DIR/captures"
CONFIG_DIR="$HOME/wireshark-config"

mkdir -p "$CAPTURE_DIR"
mkdir -p "$CONFIG_DIR"

chmod 777 "$CAPTURE_DIR"

echo "[INFO] Lab directory: $LAB_DIR"
echo "[INFO] Capture directory: $CAPTURE_DIR"
echo "[INFO] Interfaccia richiesta: $IFACE"

ROUTER_CONTAINER=$(docker ps --format "{{.Names}}" | grep -E "_router_" | head -n 1)

if [ -z "$ROUTER_CONTAINER" ]; then
  echo "[ERRORE] Nessun container router Kathara trovato."
  echo "Avvia prima il laboratorio:"
  echo "  kathara lstart"
  exit 1
fi

echo "[OK] Router trovato: $ROUTER_CONTAINER"

docker rm -f wireshark-sniffer wireshark-gui 2>/dev/null || true

docker pull "$IMAGE"

echo "[INFO] Avvio sniffer automatico..."

docker run -d \
  --name wireshark-sniffer \
  --net=container:"$ROUTER_CONTAINER" \
  --cap-add=NET_ADMIN \
  --cap-add=NET_RAW \
  -e TZ=Europe/Rome \
  -e IFACE="$IFACE" \
  -v "$CAPTURE_DIR:/captures" \
  --restart unless-stopped \
  --entrypoint /bin/bash \
  "$IMAGE" \
  -lc '
    set -e

    echo "[SNIFFER] Avviato nel namespace del router Kathara"
    echo "[SNIFFER] Interfacce disponibili:"
    ip link show

    mkdir -p /captures

    if ! touch /captures/test_write.tmp 2>/dev/null; then
      echo "[ERRORE] La directory /captures non è scrivibile."
      ls -ld /captures || true
      sleep infinity
    fi

    rm -f /captures/test_write.tmp

    FILE="/captures/router_$(date +%Y%m%d_%H%M%S).pcapng"

    echo "[SNIFFER] Cattura su interfaccia: $IFACE"
    echo "[SNIFFER] File output: $FILE"

    if command -v dumpcap >/dev/null 2>&1; then
      exec dumpcap -p -i "$IFACE" -w "$FILE" -b filesize:10240 -b files:20
    elif command -v tshark >/dev/null 2>&1; then
      exec tshark -p -i "$IFACE" -w "$FILE" -b filesize:10240 -b files:20
    else
      echo "[ERRORE] Né dumpcap né tshark sono disponibili."
      sleep infinity
    fi
  '

echo "[INFO] Avvio GUI Wireshark..."

docker run -d \
  --name wireshark-gui \
  -p 3000:3000 \
  -p 3001:3001 \
  --cap-add=NET_ADMIN \
  --cap-add=NET_RAW \
  -e PUID=1000 \
  -e PGID=1000 \
  -e TZ=Europe/Rome \
  -v "$CONFIG_DIR:/config" \
  -v "$CAPTURE_DIR:/captures" \
  --shm-size="1gb" \
  --restart unless-stopped \
  "$IMAGE"

echo ""
echo "[OK] Wireshark automatico avviato."
echo ""
echo "Log sniffer:"
echo "  docker logs -f wireshark-sniffer"
echo ""
echo "GUI Wireshark:"
echo "  http://localhost:3000"
echo ""
echo "Cartella catture host:"
echo "  $CAPTURE_DIR"
echo ""
echo "Cartella catture dentro Wireshark:"
echo "  /captures"