#!/bin/bash
set -e

IMAGE="lscr.io/linuxserver/wireshark:latest"
LAB_NAME='multi-lans'
SNIFF_NODE='r1'
IFACE="${1:-any}"

LAB_DIR="$(cd "$(dirname "$0")" && pwd)"
CAPTURE_DIR="$LAB_DIR/captures"
CONFIG_DIR="$HOME/wireshark-config-$LAB_NAME"

mkdir -p "$CAPTURE_DIR" "$CONFIG_DIR"
chmod 777 "$CAPTURE_DIR" || true

TARGET_CONTAINER=$(docker ps --format "{{.Names}}" | grep -E "^kathara_.*_${SNIFF_NODE}_" | head -n 1)

if [ -z "$TARGET_CONTAINER" ]; then
  echo "[ERRORE] Nessun container Kathara trovato per nodo: $SNIFF_NODE"
  echo "[INFO] Container Kathara attivi:"
  docker ps --format "{{.Names}}" | grep '^kathara_' || true
  echo ""
  echo "Avvia prima il lab dalla cartella:"
  echo "  kathara lstart"
  exit 1
fi

echo "[OK] Nodo sniffing: $SNIFF_NODE"
echo "[OK] Container target: $TARGET_CONTAINER"
echo "[OK] Interfaccia: $IFACE"
echo "[OK] Cartella catture: $CAPTURE_DIR"

docker rm -f "wireshark-sniffer-$LAB_NAME" "wireshark-gui-$LAB_NAME" 2>/dev/null || true

docker pull "$IMAGE"

docker run -d   --name "wireshark-sniffer-$LAB_NAME"   --net=container:"$TARGET_CONTAINER"   --cap-add=NET_ADMIN   --cap-add=NET_RAW   -e TZ=Europe/Rome   -e IFACE="$IFACE"   -e SNIFF_NODE="$SNIFF_NODE"   -v "$CAPTURE_DIR:/captures"   --restart unless-stopped   --entrypoint /bin/bash   "$IMAGE"   -lc '
    set -e
    echo "[SNIFFER] Avviato nel namespace del nodo Kathara"
    echo "[SNIFFER] Interfacce disponibili:"
    ip link show

    mkdir -p /captures
    chmod 777 /captures || true

    if ! touch /captures/test_write.tmp 2>/dev/null; then
      echo "[ERRORE] /captures non è scrivibile."
      ls -ld /captures || true
      sleep infinity
    fi
    rm -f /captures/test_write.tmp

    while true; do
      chmod -R a+rwx /captures 2>/dev/null || true
      sleep 2
    done &

    FILE="/captures/${SNIFF_NODE}_$(date +%Y%m%d_%H%M%S).pcapng"
    echo "[SNIFFER] Cattura su interfaccia: $IFACE"
    echo "[SNIFFER] File output: $FILE"

    if command -v dumpcap >/dev/null 2>&1; then
      exec dumpcap -p -i "$IFACE" -w "$FILE" -b filesize:10240 -b files:20
    elif command -v tshark >/dev/null 2>&1; then
      exec tshark -p -i "$IFACE" -w "$FILE" -b filesize:10240 -b files:20
    else
      echo "[ERRORE] Né dumpcap né tshark sono disponibili nell’immagine."
      sleep infinity
    fi
  '

docker run -d   --name "wireshark-gui-$LAB_NAME"   -p 3000:3000   -p 3001:3001   --cap-add=NET_ADMIN   --cap-add=NET_RAW   -e PUID=0   -e PGID=0   -e TZ=Europe/Rome   -v "$CONFIG_DIR:/config"   -v "$CAPTURE_DIR:/captures"   --shm-size="1gb"   --restart unless-stopped   "$IMAGE"

echo ""
echo "[OK] Wireshark automatico avviato."
echo "Log sniffer:"
echo "  docker logs -f wireshark-sniffer-$LAB_NAME"
echo "GUI Wireshark:"
echo "  http://localhost:3000"
echo "Catture host:"
echo "  $CAPTURE_DIR"
echo "Dentro Wireshark apri:"
echo "  /captures"
