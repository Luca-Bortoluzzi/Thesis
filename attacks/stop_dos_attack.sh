#!/usr/bin/env bash
set -euo pipefail

YAML_FILE="${1:-}"
shift || true

if [[ -z "${YAML_FILE}" ]]; then
  echo "Usage: $0 <topology.yml> [attacker_node ...]"
  echo "Example: $0 configs/Test_enterprise_multi_as_structured.yml pc_e pc_f"
  exit 1
fi

if [[ "$#" -gt 0 ]]; then
  ATTACKERS=("$@")
else
  ATTACKERS=("pc_e" "pc_f")
fi

for NODE in "${ATTACKERS[@]}"; do
  echo "[stop] ${NODE}"
  kathara exec "${NODE}" -- bash -lc '
    set +e
    if [ -f /tmp/kathara_dos/dos.pid ]; then
      PID=$(cat /tmp/kathara_dos/dos.pid)
      if kill -0 "$PID" 2>/dev/null; then
        kill "$PID" 2>/dev/null
        sleep 1
        kill -9 "$PID" 2>/dev/null || true
        echo "stopped PID $PID"
      else
        echo "PID file exists, but process is not running"
      fi
      rm -f /tmp/kathara_dos/dos.pid
    else
      pkill -f /tmp/kathara_dos/dos_client.py 2>/dev/null || true
      echo "no PID file; attempted cleanup by process name"
    fi
  '
done
