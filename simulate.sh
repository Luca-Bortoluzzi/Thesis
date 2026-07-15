#!/usr/bin/env bash

set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"

LAB="dos_lab"
MODE="${SIM_MODE:-full}"
TARGET="${SIM_TARGET:-server}"
SERVER="${SIM_SERVER:-server}"
SERVER_EXPLICIT=0
[[ -z "${SIM_SERVER:-}" ]] || SERVER_EXPLICIT=1
PORT="${SIM_PORT:-9000}"
CLIENTS="${SIM_CLIENTS:-}"
ATTEMPTS="${SIM_ATTEMPTS:-10}"
TIMEOUT="${SIM_TIMEOUT:-3}"
DELAY="${SIM_DELAY:-1}"
STARTUP_WAIT="${SIM_STARTUP_WAIT:-25}"
ATTACK_CONNECTIONS="${SIM_ATTACK_CONNECTIONS:-40}"
ATTACK_DURATION="${SIM_ATTACK_DURATION:-60}"
ATTACK_START_DELAY="${SIM_ATTACK_START_DELAY:-1}"
CONNECT_TIMEOUT="${SIM_CONNECT_TIMEOUT:-60}"
CONNECT_RETRIES="${SIM_CONNECT_RETRIES:-3}"
NO_START="${SIM_NO_START:-0}"
KEEP_RUNNING="${SIM_KEEP_RUNNING:-0}"
DRY_RUN="${SIM_DRY_RUN:-0}"
PLOT="${SIM_PLOT:-0}"
SIMULATION_NUMBER="${SIM_SIMULATION:-}"

LAB_DIR=""
STARTED_HERE=0
STATS_PID=""
METRICS_COLLECTED=0

log() { printf '[SIM] %s\n' "$*"; }
die() { printf '[ERROR] %s\n' "$*" >&2; exit 1; }

usage() {
    cat <<'EOF'
Usage:
  ./simulate.sh [LAB] [full|baseline|attack] [options]

Examples:
  ./simulate.sh
  ./simulate.sh dos_lab baseline
  ./simulate.sh dos_lab attack --no-start

Main options:
  --mode full|baseline|attack
  --target NODE_OR_IP      default: server [SIM_TARGET]
  --server NODE            default: server; mitigation: backend_server [SIM_SERVER]
  --port N                 default: 9000 [SIM_PORT]
  --clients "pc_a1 pc_a2"  default: all pc_* nodes
  --attempts N             default: 10
  --timeout N              measurement timeout, default: 3 [SIM_TIMEOUT]
  --delay N                delay between rounds, default: 1 [SIM_DELAY]
  --startup-wait N         default: 25 [SIM_STARTUP_WAIT]
  --attack-connections N   connections per zombie, default: 40
  --attack-duration N      default: 60, maximum: 90
  --attack-start-delay N   default: 1 [SIM_ATTACK_START_DELAY]
  --connect-timeout N      default: 60 [SIM_CONNECT_TIMEOUT]
  --connect-retries N      default: 3 [SIM_CONNECT_RETRIES]
  --simulation N           use this simulation number; default: next available
  --no-start               do not run kathara lstart
  --keep-running           do not run kathara lclean
  --plot                   generate result plots
  --dry-run                print actions without running them
  -h, --help

Advanced environment parameters:
  SIM_SERVER, SIM_PORT, SIM_TIMEOUT, SIM_DELAY, SIM_STARTUP_WAIT,
  SIM_ATTACK_START_DELAY, SIM_CONNECT_TIMEOUT, SIM_CONNECT_RETRIES,
  SIM_SIMULATION
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
    exit 0
fi

if [[ $# -gt 0 && "${1:-}" != --* && "${1:-}" != "full" && "${1:-}" != "baseline" && "${1:-}" != "attack" && "${1:-}" != "dos" ]]; then
    LAB="$1"
    shift
fi

need_value() {
    [[ $# -ge 2 && -n "${2:-}" ]] || die "Valore mancante per $1"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        full|baseline|attack|dos) MODE="$1"; shift ;;
        --mode) need_value "$@"; MODE="$2"; shift 2 ;;
        --target) need_value "$@"; TARGET="$2"; shift 2 ;;
        --server) need_value "$@"; SERVER="$2"; SERVER_EXPLICIT=1; shift 2 ;;
        --clients) need_value "$@"; CLIENTS="$2"; shift 2 ;;
        --attempts) need_value "$@"; ATTEMPTS="$2"; shift 2 ;;
        --startup-wait) need_value "$@"; STARTUP_WAIT="$2"; shift 2 ;;
        --attack-connections) need_value "$@"; ATTACK_CONNECTIONS="$2"; shift 2 ;;
        --attack-duration) need_value "$@"; ATTACK_DURATION="$2"; shift 2 ;;
        --attack-start-delay) need_value "$@"; ATTACK_START_DELAY="$2"; shift 2 ;;
        --port) need_value "$@"; PORT="$2"; shift 2 ;;
        --timeout) need_value "$@"; TIMEOUT="$2"; shift 2 ;;
        --delay) need_value "$@"; DELAY="$2"; shift 2 ;;
        --connect-timeout) need_value "$@"; CONNECT_TIMEOUT="$2"; shift 2 ;;
        --connect-retries) need_value "$@"; CONNECT_RETRIES="$2"; shift 2 ;;
        --simulation) need_value "$@"; SIMULATION_NUMBER="$2"; shift 2 ;;
        --no-start) NO_START=1; shift ;;
        --keep-running) KEEP_RUNNING=1; shift ;;
        --plot) PLOT=1; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
done

if [[ "$LAB" == "mitigation" && "$SERVER_EXPLICIT" == 0 ]]; then
    SERVER="backend_server"
fi

[[ "$MODE" == "dos" ]] && MODE="attack"
[[ "$MODE" == "full" || "$MODE" == "baseline" || "$MODE" == "attack" ]] || \
    die "Invalid mode: $MODE"

for value in "$PORT" "$ATTEMPTS" "$TIMEOUT" "$STARTUP_WAIT" "$ATTACK_CONNECTIONS" "$ATTACK_DURATION" "$ATTACK_START_DELAY" "$CONNECT_TIMEOUT" "$CONNECT_RETRIES"; do
    [[ "$value" =~ ^[0-9]+$ ]] || die "Numeric parameters must be non-negative integers."
done
if [[ -n "$SIMULATION_NUMBER" ]]; then
    [[ "$SIMULATION_NUMBER" =~ ^[0-9]+$ ]] || die "Simulation number must be a positive integer."
    SIMULATION_NUMBER=$((10#$SIMULATION_NUMBER))
    (( SIMULATION_NUMBER > 0 )) || die "Simulation number must be greater than zero."
fi
(( PORT >= 1 && PORT <= 65535 )) || die "Invalid port: $PORT"
(( ATTEMPTS > 0 && TIMEOUT > 0 && ATTACK_CONNECTIONS > 0 && ATTACK_DURATION > 0 && CONNECT_TIMEOUT > 0 && CONNECT_RETRIES > 0 )) || \
    die "Main numeric parameters must be greater than zero."
(( ATTACK_DURATION <= 90 )) || { log "Attack duration capped at 90s."; ATTACK_DURATION=90; }

find_lab() {
    if [[ -f "$LAB/lab.conf" ]]; then
        cd "$LAB" && pwd -P
    elif [[ -f "$ROOT/labs/$LAB/lab.conf" ]]; then
        cd "$ROOT/labs/$LAB" && pwd -P
    else
        return 1
    fi
}

LAB_DIR="$(find_lab)" || die "Generated lab not found: $LAB"
LAB_NAME="$(basename "$LAB_DIR")"
RESULTS_LAB_DIR="$ROOT/results/$LAB_NAME"

next_simulation_number() {
    local path suffix number highest=0
    shopt -s nullglob
    for path in "$RESULTS_LAB_DIR"/simulation_*; do
        [[ -d "$path" ]] || continue
        suffix="${path##*/simulation_}"
        [[ "$suffix" =~ ^[0-9]+$ ]] || continue
        number=$((10#$suffix))
        (( number > highest )) && highest="$number"
    done
    shopt -u nullglob
    printf '%s' "$((highest + 1))"
}

[[ -n "$SIMULATION_NUMBER" ]] || SIMULATION_NUMBER="$(next_simulation_number)"
printf -v SIMULATION_NAME 'simulation_%04d' "$SIMULATION_NUMBER"
SIMULATION_DIR="$RESULTS_LAB_DIR/$SIMULATION_NAME"
SERVER_METRICS_SOURCE="$LAB_DIR/shared/server_metrics.csv"
SERVER_SCENARIO_FILE="$LAB_DIR/shared/server_metrics_scenario"
SERVER_METRICS_RESULT="$SIMULATION_DIR/server_metrics.csv"
CONTAINER_METRICS_RESULT="$SIMULATION_DIR/server_container_metrics.csv"

mapfile -t NODES < <(
    sed -nE 's/^[[:space:]]*([A-Za-z0-9_.-]+)\[[^]]+\].*/\1/p' "$LAB_DIR/lab.conf" |
        awk '!seen[$0]++'
)
(( ${#NODES[@]} > 0 )) || die "No nodes found in lab.conf"

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
has_node "$SERVER" || die "Server node '$SERVER' is missing from the lab."
[[ -f "$LAB_DIR/$SERVER/service.py" ]] || die "$SERVER/service.py is missing from the lab."
has_node "$ATTACKER" || die "Node '$ATTACKER' is missing from the lab."
[[ -f "$LAB_DIR/$ATTACKER/attacker.py" ]] || die "$ATTACKER/attacker.py is missing from the lab."

ZOMBIES=()
ZOMBIE_IPS=()
for node in "${NODES[@]}"; do
    [[ "$node" == zombie* ]] || continue
    [[ -f "$LAB_DIR/$node/zombie.py" ]] || die "$node/zombie.py is missing from the lab."
    ip="$(node_ip "$node")"
    [[ -n "$ip" ]] || die "IP address missing in $node.startup"
    ZOMBIES+=("$node")
    ZOMBIE_IPS+=("$ip")
done
(( ${#ZOMBIES[@]} > 0 )) || die "The lab contains no zombie* nodes."

if has_node "$TARGET"; then
    TARGET_IP="$(node_ip "$TARGET")"
else
    TARGET_IP=""
    for node in "${NODES[@]}"; do
        [[ "$(node_ip "$node")" == "$TARGET" ]] && TARGET_IP="$TARGET"
    done
fi
[[ -n "$TARGET_IP" ]] || die "Target '$TARGET' was not found among lab nodes/IP addresses."

if [[ -z "$CLIENTS" ]]; then
    for node in "${NODES[@]}"; do
        [[ "$node" =~ ^pc[_-] ]] && CLIENTS+="${CLIENTS:+ }$node"
    done
fi
[[ -n "$CLIENTS" ]] || die "No clients found; use --clients \"pc_a1 pc_a2\"."
for client in $CLIENTS; do
    has_node "$client" || die "Client is not present in the lab: $client"
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

emit_remote_script() {
    local body="$1"
    printf '#!/bin/sh\n'
    printf 'sim_remote_main() {\n'
    printf '%s\n' "$body"
    printf '}\n'
    printf 'sim_remote_main\n'
    printf 'rc=$?\n'
    printf 'printf "__SIM_RC__:%%s\\n" "$rc"\n'
    printf 'exit "$rc"\n'
}

node_run() {
    local node="$1"
    local title="$2"
    local body="${3:-}"
    local output marker rc attempt tool_rc remote_script

    if (( $# < 3 )); then
        body="$(cat)"
    fi

    log "kathara exec $node: $title"
    if (( DRY_RUN )); then
        emit_remote_script "$body" | /bin/sh -n || die "Invalid remote script for $node."
        return 0
    fi

    remote_script="$LAB_DIR/shared/.simulate_${node}.sh"
    emit_remote_script "$body" > "$remote_script"

    if ! /bin/sh -n "$remote_script"; then
        die "Invalid remote script for $node: $remote_script"
    fi

    for (( attempt = 1; attempt <= CONNECT_RETRIES; attempt++ )); do
        (( attempt == 1 )) || log "Retrying kathara exec $node ($attempt/$CONNECT_RETRIES)."

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
            (( rc == 0 )) || die "Command failed inside $node (exit code $rc)."
            return 0
        fi

        (( tool_rc == 124 )) && log "kathara exec timed out for $node after ${CONNECT_TIMEOUT}s."
        (( attempt < CONNECT_RETRIES )) && sleep 2
    done

    die "kathara exec $node did not return a command result after $CONNECT_RETRIES attempts."
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

stop_container_sampler() {
    if [[ -n "$STATS_PID" ]]; then
        kill "$STATS_PID" 2>/dev/null || true
        wait "$STATS_PID" 2>/dev/null || true
        STATS_PID=""
    fi
}

container_metrics_has_samples() {
    [[ -f "$CONTAINER_METRICS_RESULT" ]] || return 1
    (( $(wc -l < "$CONTAINER_METRICS_RESULT") > 1 ))
}

collect_metrics() {
    (( METRICS_COLLECTED )) && return 0
    METRICS_COLLECTED=1
    (( DRY_RUN )) && return 0

    mkdir -p "$SIMULATION_DIR"
    if [[ -s "$SERVER_METRICS_SOURCE" ]]; then
        cp "$SERVER_METRICS_SOURCE" "$SERVER_METRICS_RESULT"
        log "Server metrics: $SERVER_METRICS_RESULT"
    else
        log "Server metrics unavailable: $SERVER_METRICS_SOURCE is empty or missing."
    fi
    if container_metrics_has_samples; then
        log "Container metrics: $CONTAINER_METRICS_RESULT"
    fi
}

cleanup() {
    local rc=$?
    trap - EXIT INT TERM
    stop_container_sampler
    collect_metrics
    cleanup_remote_scripts
    if (( STARTED_HERE && ! KEEP_RUNNING )); then
        log "Cleaning up the Kathara lab."
        run_in_lab kathara lclean
    elif (( STARTED_HERE )); then
        log "Lab left running: $LAB_DIR"
    fi
    exit "$rc"
}

trap cleanup EXIT
trap 'exit 130' INT TERM

if (( ! DRY_RUN )); then
    command -v kathara >/dev/null || die "kathara command not found."
    command -v docker >/dev/null || die "docker command not found."
    command -v timeout >/dev/null || die "timeout command not found."
fi

log "Lab: $LAB_DIR"
log "Simulation: $SIMULATION_NAME"
log "Results: $SIMULATION_DIR"
log "Mode: $MODE"
log "Server: $SERVER"
log "Target: $TARGET_IP:$PORT"
log "Client: $CLIENTS"
log "Zombie: ${ZOMBIES[*]} (${ZOMBIE_IPS[*]})"

if (( ! DRY_RUN )); then
    mkdir -p "$LAB_DIR/shared" "$SIMULATION_DIR"
    : > "$SERVER_METRICS_SOURCE"
    printf 'idle\n' > "$SERVER_SCENARIO_FILE"
    printf 'timestamp,scenario,container,cpu_percent,memory_usage,memory_percent,pids\n' > "$CONTAINER_METRICS_RESULT"
fi

if (( DRY_RUN )); then
    log "Would update $LAB_DIR/$ATTACKER/zombies.txt"
else
    printf '%s\n' "${ZOMBIE_IPS[@]}" > "$LAB_DIR/$ATTACKER/zombies.txt"
fi

if (( ! NO_START )); then
    STARTED_HERE=1
    start_lab
    if (( STARTUP_WAIT > 0 )); then
        log "Waiting ${STARTUP_WAIT}s."
        (( DRY_RUN )) || sleep "$STARTUP_WAIT"
    fi
fi

start_server() {
    node_run "$SERVER" "start service.py when needed" <<SIM_SERVER_BODY
SCRIPT=
LOG=/tmp/service.log
SERVICE_PORT='$PORT'
METRICS=/shared/server_metrics.csv

for candidate in /hostlab/service.py '/hostlab/$SERVER/service.py' /shared/service.py; do
    [ -f "\$candidate" ] && SCRIPT="\$candidate" && break
done
[ -n "\$SCRIPT" ] || { echo '[ERROR] service.py not found in /hostlab or /shared'; return 1; }

service_ready() {
    SERVICE_PORT="\$SERVICE_PORT" python3 -c 'import os, socket; s = socket.create_connection(("127.0.0.1", int(os.environ["SERVICE_PORT"])), 1); s.close()' >/dev/null 2>&1
}

if service_ready; then
    echo '[OK] service.py is already active and reachable'
    sleep 1
    [ -s "\$METRICS" ] || echo '[WARN] service.py is active but has not produced structured metrics'
    return 0
fi

SERVICE_PORT='$PORT' SERVICE_MAX_ACTIVE=20 SERVICE_BACKLOG=20 SERVICE_METRICS_PATH="\$METRICS" SERVICE_METRICS_SCENARIO_PATH=/shared/server_metrics_scenario nohup python3 -u "\$SCRIPT" > "\$LOG" 2>&1 < /dev/null &
sleep 1
service_ready || {
    echo '[ERROR] service.py did not start'
    tail -n 20 "\$LOG" 2>/dev/null || true
    return 1
}
echo '[OK] service.py started and reachable'
SIM_SERVER_BODY
}

latest_csv() {
    local scenario="$1" file
    file="$SIMULATION_DIR/connection_results_${scenario}.csv"
    if [[ -f "$file" ]]; then
        printf '%s' "$file"
    fi
    return 0
}

find_server_container() {
    local container
    while IFS= read -r container; do
        case "$container" in
            kathara_*_"$SERVER"_*) printf '%s' "$container"; return 0 ;;
        esac
    done < <(docker ps --format '{{.Names}}' 2>/dev/null)
    return 1
}

set_server_scenario() {
    local scenario="$1"
    (( DRY_RUN )) && return 0
    printf '%s\n' "$scenario" > "$SERVER_SCENARIO_FILE"
}

start_container_sampler() {
    local scenario="$1" container initial_lines current_lines attempt
    (( DRY_RUN )) && return 0
    container="$(find_server_container || true)"
    if [[ -z "$container" ]]; then
        log "Container for server node '$SERVER' not found; CPU and memory will not be sampled."
        return 0
    fi

    initial_lines="$(wc -l < "$CONTAINER_METRICS_RESULT")"
    (
        local sample cpu memory memory_percent pids timestamp
        while true; do
            sample="$(docker stats --no-stream --format '{{.CPUPerc}}|{{.MemUsage}}|{{.MemPerc}}|{{.PIDs}}' "$container" 2>/dev/null)" || break
            [[ -n "$sample" ]] || break
            IFS='|' read -r cpu memory memory_percent pids <<< "$sample"
            timestamp="$(date '+%Y-%m-%dT%H:%M:%S.%3N%:z')"
            printf '"%s","%s","%s","%s","%s","%s","%s"\n' \
                "$timestamp" "$scenario" "$container" "$cpu" "$memory" "$memory_percent" "$pids" \
                >> "$CONTAINER_METRICS_RESULT"
            sleep 1
        done
    ) &
    STATS_PID=$!

    for (( attempt = 1; attempt <= 30; attempt++ )); do
        current_lines="$(wc -l < "$CONTAINER_METRICS_RESULT")"
        (( current_lines > initial_lines )) && return 0
        sleep 0.1
    done
    log "The first Docker metrics sample was not ready after 3 seconds."
}

measure() {
    local scenario="$1" sampler_ready="${2:-0}" csv measurement_rc=0
    if (( ! sampler_ready )); then
        set_server_scenario "$scenario"
        start_container_sampler "$scenario"
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
        --simulation "$SIMULATION_NUMBER" \
        --output-dir "$ROOT" \
        --non-interactive || measurement_rc=$?
    stop_container_sampler
    (( measurement_rc == 0 )) || return "$measurement_rc"

    (( DRY_RUN )) && return 0
    csv="$(latest_csv "$scenario")"
    [[ -n "$csv" ]] || die "No CSV was produced for '$scenario'."
    if (( PLOT )); then
        run "$ROOT/show_connection_results.py" "$csv" --plot
    else
        run "$ROOT/show_connection_results.py" "$csv"
    fi
}

start_zombies() {
    local node
    for node in "${ZOMBIES[@]}"; do
        node_run "$node" "start zombie.py when needed" <<SIM_ZOMBIE_BODY
SCRIPT=
LOG=/tmp/zombie.log

for candidate in /hostlab/zombie.py '/hostlab/$node/zombie.py' /shared/zombie.py; do
    [ -f "\$candidate" ] && SCRIPT="\$candidate" && break
done
[ -n "\$SCRIPT" ] || { echo '[ERROR] zombie.py not found in /hostlab or /shared'; return 1; }

zombie_ready() {
    python3 -c 'import socket; s = socket.create_connection(("127.0.0.1", 9999), 1); s.sendall(b"PING\n"); s.recv(1024); s.close()' >/dev/null 2>&1
}

if zombie_ready; then
    echo '[OK] zombie.py is already running'
    return 0
fi

nohup python3 -u "\$SCRIPT" > "\$LOG" 2>&1 < /dev/null &
sleep 1
zombie_ready || {
    echo '[ERROR] zombie.py did not start'
    tail -n 20 "\$LOG" 2>/dev/null || true
    return 1
}
    echo '[OK] zombie.py started'
SIM_ZOMBIE_BODY
    done
}

start_attack() {
    node_run "$ATTACKER" "start controlled attack" <<SIM_ATTACK_BODY
SCRIPT=
for candidate in /hostlab/attacker.py '/hostlab/$ATTACKER/attacker.py' /shared/attacker.py; do
    [ -f "\$candidate" ] && SCRIPT="\$candidate" && break
done
[ -n "\$SCRIPT" ] || { echo '[ERROR] attacker.py not found in /hostlab or /shared'; return 1; }
python3 "\$SCRIPT" '$TARGET_IP' '$PORT' '$ATTACK_CONNECTIONS' '$ATTACK_DURATION'
SIM_ATTACK_BODY
}

measure_during_attack() {
    local attack_pid measure_rc=0 attack_rc=0

    log "Starting DoS measurements; zombies will start after ${ATTACK_START_DELAY}s."
    if (( DRY_RUN )); then
        measure dos
        start_attack
        return 0
    fi

    set_server_scenario dos
    start_container_sampler dos
    (
        trap - EXIT INT TERM
        (( ATTACK_START_DELAY == 0 )) || sleep "$ATTACK_START_DELAY"
        start_attack
    ) &
    attack_pid=$!

    measure dos 1 || measure_rc=$?
    wait "$attack_pid" || attack_rc=$?

    (( attack_rc == 0 )) || die "Attack startup failed (exit code $attack_rc)."
    return "$measure_rc"
}

start_server
[[ "$MODE" == "attack" ]] || measure baseline

if [[ "$MODE" != "baseline" ]]; then
    start_zombies
    measure_during_attack
fi

collect_metrics
if [[ -s "$SERVER_METRICS_RESULT" ]]; then
    if container_metrics_has_samples; then
        run python3 "$ROOT/show_server_metrics.py" "$SERVER_METRICS_RESULT" --container "$CONTAINER_METRICS_RESULT"
    else
        run python3 "$ROOT/show_server_metrics.py" "$SERVER_METRICS_RESULT"
    fi
fi

log "Simulation complete."
