#!/bin/bash
LAB_NAME='multi-lans'
docker rm -f "wireshark-sniffer-$LAB_NAME" "wireshark-gui-$LAB_NAME" 2>/dev/null || true
echo "[OK] Wireshark fermato per lab: $LAB_NAME"
