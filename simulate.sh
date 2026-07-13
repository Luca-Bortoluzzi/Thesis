#!/usr/bin/env bash

set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"

LAB="dos_lab"
MODE="${SIM_MODE:-full}"
TARGET="${SIM_TARGET:-server}"
SERVER="${SIM_SERVER:-server}"
PORT="${SIM_PORT:-9000}"
CLIENTS="${SIM_CLIENTS:-}"
ATTEMPTS="${SIM_ATTEMPTS:-10}"
TIMEOUT="${SIM_TIMEOUT:-3}"
DELAY="${SIM_DELAY:-1}"
STARTUP_WAIT="${SIM_STARTUP_WAIT:-25}"
CONNECTIONS_WAIT="${SIM_CONNECTIONS_WAIT:-30}"
ATTACK_CONNECTIONS="${SIM_ATTACK_CONNECTIONS:-40}"
ATTACK_DURATION="${SIM_ATTACK_DURATION:-60}"
CONNECT_TIMEOUT="${SIM_CONNECT_TIMEOUT:-60}"
CONNECT_RETRIES="${SIM_CONNECT_RETRIES:-3}"
NO_START="${SIM_NO_START:-0}"
KEEP_RUNNING="${SIM_KEEP_RUNNING:-0}"
DRY_RUN="${SIM_DRY_RUN:-0}"
PLOT="${SIM_PLOT:-0}"

LAB_DIR=""
STARTED_HERE=0

log() { printf '[SIM] %s\n' "$*"; }
die() { printf '[ERRORE] %s\n' "$*" >&2; exit 1; }

usage() {
    cat <<'EOF'
Uso:
  ./simulate.sh [LAB] [full|baseline|attack] [opzioni]

Esempi:
  ./simulate.sh
  ./simulate.sh dos_lab baseline
  ./simulate.sh dos_lab attack --no-start

Opzioni principali:
  --mode full|baseline|attack
  --target NODO_O_IP       default: server
  --server NODO            nodo servizio, default: server
  --clients "pc_a1 pc_a2"  default: tutti i nodi pc_*
  --attempts N             default: 10
  --connections-wait N     attesa prima dei test TCP, default: 30
  --attack-connections N   default: 40
  --attack-duration N      default: 60, massimo: 90
  --no-start               non esegue kathara lstart
  --keep-running           non esegue kathara lclean
  --plot                   genera il grafico dei risultati
  --dry-run                mostra cosa farebbe
  -h, --help

Parametri avanzati via ambiente:
  SIM_SERVER, SIM_PORT, SIM_TIMEOUT, SIM_DELAY, SIM_STARTUP_WAIT,
  SIM_CONNECTIONS_WAIT, SIM_CONNECT_TIMEOUT, SIM_CONNECT_RETRIES
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
    exit 0
fi

if [[ $# -gt 0 && "${1:-}" != --* && "${1:-}" != "full" && "${1:-}" != "baseline" && "${1:-}" != "attack" ]]; then
    LAB="$1"
    shift
fi

need_value() {
    [[ $# -ge 2 && -n "${2:-}" ]] || die "Valore mancante per $1"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        full|baseline|attack) MODE="$1"; shift ;;
        --mode) need_value "$@"; MODE="$2"; shift 2 ;;
        --target) need_value "$@"; TARGET="$2"; shift 2 ;;
        --server) need_value "$@"; SERVER="$2"; shift 2 ;;
        --clients) need_value "$@"; CLIENTS="$2"; shift 2 ;;
        --attempts) need_value "$@"; ATTEMPTS="$2"; shift 2 ;;
        --connections-wait) need_value "$@"; CONNECTIONS_WAIT="$2"; shift 2 ;;
        --attack-connections) need_value "$@"; ATTACK_CONNECTIONS="$2"; shift 2 ;;
        --attack-duration) need_value "$@"; ATTACK_DURATION="$2"; shift 2 ;;
        --port) need_value "$@"; PORT="$2"; shift 2 ;;
        --timeout) need_value "$@"; TIMEOUT="$2"; shift 2 ;;
        --delay) need_value "$@"; DELAY="$2"; shift 2 ;;
        --no-start) NO_START=1; shift ;;
        --keep-running) KEEP_RUNNING=1; shift ;;
        --plot) PLOT=1; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "Argomento sconosciuto: $1" ;;
    esac
done

[[ "$MODE" == "full" || "$MODE" == "baseline" || "$MODE" == "attack" ]] || \
    die "Modalita' non valida: $MODE"

for value in "$PORT" "$ATTEMPTS" "$TIMEOUT" "$STARTUP_WAIT" "$CONNECTIONS_WAIT" "$ATTACK_CONNECTIONS" "$ATTACK_DURATION" "$CONNECT_TIMEOUT" "$CONNECT_RETRIES"; do
    [[ "$value" =~ ^[0-9]+$ ]] || die "I parametri numerici devono essere interi non negativi."
done
(( PORT >= 1 && PORT <= 65535 )) || die "Porta non valida: $PORT"
(( ATTEMPTS > 0 && TIMEOUT > 0 && ATTACK_CONNECTIONS > 0 && ATTACK_DURATION > 0 && CONNECT_TIMEOUT > 0 && CONNECT_RETRIES > 0 )) || \
    die "I parametri numerici principali devono essere maggiori di zero."
(( ATTACK_DURATION <= 90 )) || { log "Durata attacco ridotta a 90s."; ATTACK_DURATION=90; }

find_lab() {
    if [[ -f "$LAB/lab.conf" ]]; then
        cd "$LAB" && pwd -P
    elif [[ -f "$ROOT/labs/$LAB/lab.conf" ]]; then
        cd "$ROOT/labs/$LAB" && pwd -P
    else
        return 1
    fi
}

LAB_DIR="$(find_lab)" || die "Laboratorio gia' generato non trovato: $LAB"

mapfile -t NODES < <(
    sed -nE 's/^[[:space:]]*([A-Za-z0-9_.-]+)\[[^]]+\].*/\1/p' "$LAB_DIR/lab.conf" |
        awk '!seen[$0]++'
)
(( ${#NODES[@]} > 0 )) || die "Nessun nodo trovato in lab.conf"

has_node() {
    local node
    for node in "${NODES[@]}"; do
        [[ "$node" == "$1" ]] && return 0
    done
    return 1
}

node_ip() {
    awk '$1 == "ip" && $2 == "address" && $3 == "add" {split($4, ip, "/"); print ip[1]; exit}' \
        "$LAB_DIR/$1.startup" 2>/dev/null
}

ATTACKER="attacker"
has_node "$SERVER" || die "Nel lab manca il nodo server '$SERVER'."
[[ -f "$LAB_DIR/$SERVER/service.py" ]] || die "Manca $SERVER/service.py nel lab."
has_node "$ATTACKER" || die "Nel lab manca il nodo '$ATTACKER'."
[[ -f "$LAB_DIR/$ATTACKER/attacker.py" ]] || die "Manca $ATTACKER/attacker.py nel lab."

ZOMBIES=()
ZOMBIE_IPS=()
for node in "${NODES[@]}"; do
    [[ "$node" == zombie* ]] || continue
    [[ -f "$LAB_DIR/$node/zombie.py" ]] || die "Manca $node/zombie.py nel lab."
    ip="$(node_ip "$node")"
    [[ -n "$ip" ]] || die "Manca l'IP in $node.startup"
    ZOMBIES+=("$node")
    ZOMBIE_IPS+=("$ip")
done
(( ${#ZOMBIES[@]} > 0 )) || die "Nel lab non ci sono nodi zombie*."

if has_node "$TARGET"; then
    TARGET_IP="$(node_ip "$TARGET")"
else
    TARGET_IP=""
    for node in "${NODES[@]}"; do
        [[ "$(node_ip "$node")" == "$TARGET" ]] && TARGET_IP="$TARGET"
    done
fi
[[ -n "$TARGET_IP" ]] || die "Target '$TARGET' non trovato tra i nodi/IP del laboratorio."

if [[ -z "$CLIENTS" ]]; then
    for node in "${NODES[@]}"; do
        [[ "$node" =~ ^pc[_-] ]] && CLIENTS+="${CLIENTS:+ }$node"
    done
fi
[[ -n "$CLIENTS" ]] || die "Nessun client trovato; usa --clients \"pc_a1 pc_a2\"."
for client in $CLIENTS; do
    has_node "$client" || die "Client non presente nel lab: $client"
done

run() {
    printf '[SIM] $ '
    printf '%q ' "$@"
    printf '\n'
    (( DRY_RUN )) || "$@"
}

run_in_lab() {
    printf '[SIM] $ (cd %q && ' "$LAB_DIR"
    printf '%q ' "$@"
    printf ')\n'
    (( DRY_RUN )) || (cd "$LAB_DIR" && "$@")
}

start_lab() {
    printf '[SIM] $ (cd %q && yes n | kathara lstart --noterminals)\n' "$LAB_DIR"
    (( DRY_RUN )) && return 0
    (cd "$LAB_DIR" && set +o pipefail && yes n | kathara lstart --noterminals)
}

node_run() {
    local node="$1"
    local title="$2"
    local body="$3"
    local output marker rc attempt tool_rc remote_script

    log "kathara exec $node: $title"
    (( DRY_RUN )) && return 0

    remote_script="$LAB_DIR/shared/.simulate_${node}.sh"
    {
        printf '#!/bin/sh\n'
        printf 'sim_remote_main() {\n'
        printf '%s\n' "$body"
        printf '}\n'
        printf 'sim_remote_main\n'
        printf 'rc=$?\n'
        printf 'printf "__SIM_RC__:%%s\\n" "$rc"\n'
        printf 'exit "$rc"\n'
    } > "$remote_script"

    for (( attempt = 1; attempt <= CONNECT_RETRIES; attempt++ )); do
        (( attempt == 1 )) || log "Riprovo kathara exec $node ($attempt/$CONNECT_RETRIES)."

        if output="$(cd "$LAB_DIR" && timeout "${CONNECT_TIMEOUT}s" kathara exec "$node" /bin/sh "/shared/.simulate_${node}.sh")"; then
            tool_rc=0
        else
            tool_rc=$?
        fi

        output="${output//$'\r'/}"
        printf '%s\n' "$output"
        marker="$(printf '%s\n' "$output" | awk 'match($0, /__SIM_RC__:[0-9]+/) {m=substr($0,RSTART,RLENGTH)} END {print m}')"

        if [[ -n "$marker" ]]; then
            rc="${marker##*:}"
            (( rc == 0 )) || die "Comando fallito dentro $node (codice $rc)."
            return 0
        fi

        (( tool_rc == 124 )) && log "Timeout di kathara exec $node dopo ${CONNECT_TIMEOUT}s."
        (( attempt < CONNECT_RETRIES )) && sleep 2
    done

    die "kathara exec $node non ha restituito l'esito del comando dopo $CONNECT_RETRIES tentativi."
}

cleanup_remote_scripts() {
    local file
    (( DRY_RUN )) && return 0
    shopt -s nullglob
    for file in "$LAB_DIR"/shared/.simulate_*.sh; do
        rm -f "$file"
    done
    shopt -u nullglob
}

cleanup() {
    local rc=$?
    trap - EXIT INT TERM
    cleanup_remote_scripts
    if (( STARTED_HERE && ! KEEP_RUNNING )); then
        log "Pulizia laboratorio Kathara."
        run_in_lab kathara lclean
    elif (( STARTED_HERE )); then
        log "Laboratorio lasciato attivo: $LAB_DIR"
    fi
    exit "$rc"
}

trap cleanup EXIT
trap 'exit 130' INT TERM

if (( ! DRY_RUN )); then
    command -v kathara >/dev/null || die "Comando kathara non trovato."
    command -v timeout >/dev/null || die "Comando timeout non trovato."
fi

log "Lab: $LAB_DIR"
log "Mode: $MODE"
log "Server: $SERVER"
log "Target: $TARGET_IP:$PORT"
log "Client: $CLIENTS"
log "Zombie: ${ZOMBIES[*]} (${ZOMBIE_IPS[*]})"

(( DRY_RUN )) || mkdir -p "$LAB_DIR/shared"

if (( DRY_RUN )); then
    log "Aggiornerei $LAB_DIR/$ATTACKER/zombies.txt"
else
    printf '%s\n' "${ZOMBIE_IPS[@]}" > "$LAB_DIR/$ATTACKER/zombies.txt"
fi

if (( ! NO_START )); then
    STARTED_HERE=1
    start_lab
    if (( STARTUP_WAIT > 0 )); then
        log "Attendo ${STARTUP_WAIT}s."
        (( DRY_RUN )) || sleep "$STARTUP_WAIT"
    fi
fi

start_server() {
    node_run "$SERVER" "avvio service.py se necessario" "
SCRIPT=
LOG=/tmp/service.log
SERVICE_PORT='$PORT'

for candidate in /hostlab/service.py '/hostlab/$SERVER/service.py' /shared/service.py; do
    [ -f \"\$candidate\" ] && SCRIPT=\"\$candidate\" && break
done
[ -n \"\$SCRIPT\" ] || { echo '[ERRORE] service.py non trovato in /hostlab o /shared'; return 1; }

service_ready() {
    SERVICE_PORT=\"\$SERVICE_PORT\" python3 -c 'import os, socket; s = socket.create_connection((\"127.0.0.1\", int(os.environ[\"SERVICE_PORT\"])), 1); s.close()' >/dev/null 2>&1
}

if service_ready; then
    echo '[OK] service.py gia attivo e raggiungibile'
    return 0
fi

SERVICE_PORT='$PORT' SERVICE_MAX_ACTIVE=20 SERVICE_BACKLOG=20 nohup python3 -u \"\$SCRIPT\" > \"\$LOG\" 2>&1 < /dev/null &
sleep 1
service_ready || {
    echo '[ERRORE] service.py non avviato'
    tail -n 20 \"\$LOG\" 2>/dev/null || true
    return 1
}
echo '[OK] service.py avviato e raggiungibile'
"
}

latest_csv() {
    local scenario="$1" file latest=""
    shopt -s nullglob
    for file in "$ROOT"/results/connection_results_"$scenario"_*.csv; do
        [[ -z "$latest" || "$file" -nt "$latest" ]] && latest="$file"
    done
    shopt -u nullglob
    printf '%s' "$latest"
}

measure() {
    local scenario="$1" csv
    if (( CONNECTIONS_WAIT > 0 )); then
        log "Attendo ${CONNECTIONS_WAIT}s prima delle connessioni ($scenario)."
        (( DRY_RUN )) || sleep "$CONNECTIONS_WAIT"
    fi

    run "$ROOT/run_connection_tests.py" \
        --lab "$LAB_DIR" \
        --target "$TARGET" \
        --port "$PORT" \
        --clients "$CLIENTS" \
        --scenario "$scenario" \
        --attempts "$ATTEMPTS" \
        --timeout "$TIMEOUT" \
        --delay "$DELAY" \
        --non-interactive

    (( DRY_RUN )) && return 0
    csv="$(latest_csv "$scenario")"
    [[ -n "$csv" ]] || die "Nessun CSV prodotto per '$scenario'."
    if (( PLOT )); then
        run "$ROOT/show_connection_results.py" "$csv" --plot
    else
        run "$ROOT/show_connection_results.py" "$csv"
    fi
}

start_zombies() {
    local node
    for node in "${ZOMBIES[@]}"; do
        node_run "$node" "avvio zombie.py se necessario" "
SCRIPT=
LOG=/tmp/zombie.log

for candidate in /hostlab/zombie.py '/hostlab/$node/zombie.py' /shared/zombie.py; do
    [ -f \"\$candidate\" ] && SCRIPT=\"\$candidate\" && break
done
[ -n \"\$SCRIPT\" ] || { echo '[ERRORE] zombie.py non trovato in /hostlab o /shared'; return 1; }
if ps w 2>/dev/null | grep "[p]ython3.*zombie.py" >/dev/null; then
    echo "[OK] zombie.py gia attivo"
    return 0
fi

nohup python3 -u \"\$SCRIPT\" > \"\$LOG\" 2>&1 < /dev/null &
sleep 1
ps w 2>/dev/null | grep "[p]ython3.*zombie.py" >/dev/null || {
    echo "[ERRORE] zombie.py non avviato"
    tail -n 20 \"\$LOG\" 2>/dev/null || true
    return 1
}
echo "[OK] zombie.py avviato"
"
    done
}

start_attack() {
    node_run "$ATTACKER" "avvio attacco controllato" "
SCRIPT=
for candidate in /hostlab/attacker.py '/hostlab/$ATTACKER/attacker.py' /shared/attacker.py; do
    [ -f \"\$candidate\" ] && SCRIPT=\"\$candidate\" && break
done
[ -n \"\$SCRIPT\" ] || { echo '[ERRORE] attacker.py non trovato in /hostlab o /shared'; return 1; }
python3 \"\$SCRIPT\" '$TARGET_IP' '$PORT' '$ATTACK_CONNECTIONS' '$ATTACK_DURATION'
"
}

start_server

[[ "$MODE" == "attack" ]] || measure baseline

if [[ "$MODE" != "baseline" ]]; then
    start_zombies
    start_attack
    measure dos
fi

log "Simulazione completata."
