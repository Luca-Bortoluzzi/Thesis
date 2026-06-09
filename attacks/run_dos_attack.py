#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_ATTACKERS = ["pc_e", "pc_f"]
DEFAULT_DURATION = 30
DEFAULT_WORKERS = 20
DEFAULT_TIMEOUT = 1.0
DEFAULT_PAYLOAD_SIZE = 64
DEFAULT_DELAY_MS = 20
DEFAULT_MODE = "connect-flood"
MAX_DURATION_SECONDS = 120
MAX_WORKERS_PER_ATTACKER = 100

SCRIPT_DIR = Path(__file__).resolve().parent
DOS_CLIENT_PATH = SCRIPT_DIR / "dos_client.py"


def load_yaml(path: Path) -> Dict[str, Any]:
    try:
        import yaml  # type: ignore
    except ImportError as exc:
        raise SystemExit("Missing dependency: PyYAML. Install with: python3 -m pip install pyyaml") from exc
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise SystemExit("Invalid YAML root")
    return data


def choose(value: Optional[Any], fallback: Any) -> Any:
    return fallback if value is None else value


def derive_config(data: Dict[str, Any], args: argparse.Namespace) -> Dict[str, Any]:
    nodes = data.get("nodes", {}) or {}
    connection_tests = data.get("connection_tests", {}) or {}
    dos_tests = data.get("dos_tests", {}) or {}
    target_ip = args.target or dos_tests.get("target_ip") or connection_tests.get("target_ip")
    port = args.port or dos_tests.get("port") or connection_tests.get("port")
    if not target_ip or not port:
        raise SystemExit("Target IP and port not found. Use YAML dos_tests/connection_tests or CLI options.")
    attackers = args.attackers or dos_tests.get("attackers") or [n for n in DEFAULT_ATTACKERS if n in nodes]
    if not attackers:
        raise SystemExit("No attackers found. Use --attackers pc_e pc_f or YAML dos_tests.attackers.")
    duration = int(args.duration or dos_tests.get("duration") or DEFAULT_DURATION)
    workers = int(args.workers or dos_tests.get("workers_per_attacker") or DEFAULT_WORKERS)
    timeout = float(args.timeout or dos_tests.get("connection_timeout") or DEFAULT_TIMEOUT)
    payload_size = int(args.payload_size or dos_tests.get("payload_size") or DEFAULT_PAYLOAD_SIZE)
    delay_ms = int(choose(args.delay_ms, dos_tests.get("delay_between_connections_ms", DEFAULT_DELAY_MS)))
    mode = args.mode or dos_tests.get("mode") or DEFAULT_MODE
    if not 1 <= duration <= MAX_DURATION_SECONDS:
        raise SystemExit(f"Refusing duration={duration}. Allowed: 1..{MAX_DURATION_SECONDS}")
    if not 1 <= workers <= MAX_WORKERS_PER_ATTACKER:
        raise SystemExit(f"Refusing workers={workers}. Allowed: 1..{MAX_WORKERS_PER_ATTACKER}")
    if mode not in {"connect-flood", "hold-open"}:
        raise SystemExit("Invalid mode")
    return {
        "lab_name": data.get("lab_name", "unknown_lab"),
        "attackers": attackers,
        "target_ip": str(target_ip),
        "port": int(port),
        "duration": duration,
        "workers_per_attacker": workers,
        "timeout": timeout,
        "payload_size": payload_size,
        "delay_ms": delay_ms,
        "mode": mode,
    }


def run_cmd(cmd: List[str], dry_run: bool) -> subprocess.CompletedProcess[str] | None:
    print("[run] " + " ".join(cmd))
    if dry_run:
        return None
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)


def kathara_exec_cmd(node: str, shell_cmd: str) -> List[str]:
    return ["kathara", "exec", node, "--", "bash", "-lc", shell_cmd]


def inject_and_start(node: str, cfg: Dict[str, Any], client_b64: str, dry_run: bool) -> None:
    remote_dir = "/tmp/kathara_dos"
    remote_client = f"{remote_dir}/dos_client.py"
    remote_log = f"{remote_dir}/dos_client_{node}.json"
    remote_stdout = f"{remote_dir}/dos_client_{node}.out"
    remote_pid = f"{remote_dir}/dos.pid"
    shell_cmd = f'''
set -eu
mkdir -p {remote_dir}
python3 -c 'import base64; from pathlib import Path; Path("{remote_client}").write_bytes(base64.b64decode("{client_b64}"))'
chmod +x {remote_client}
if [ -f {remote_pid} ] && kill -0 $(cat {remote_pid}) 2>/dev/null; then
  echo "A DoS lab process is already running on this node with PID $(cat {remote_pid})"
  exit 0
fi
nohup python3 {remote_client} \
  --target {cfg["target_ip"]} \
  --port {cfg["port"]} \
  --duration {cfg["duration"]} \
  --workers {cfg["workers_per_attacker"]} \
  --timeout {cfg["timeout"]} \
  --payload-size {cfg["payload_size"]} \
  --delay-ms {cfg["delay_ms"]} \
  --mode {cfg["mode"]} \
  --log-file {remote_log} \
  > {remote_stdout} 2>&1 &
echo $! > {remote_pid}
echo "started controlled DoS lab process on {node}, pid=$(cat {remote_pid}), log={remote_log}"
'''.strip()
    result = run_cmd(kathara_exec_cmd(node, shell_cmd), dry_run)
    if result is not None:
        if result.stdout:
            print(result.stdout.strip())
        if result.returncode != 0:
            if result.stderr:
                print(result.stderr.strip(), file=sys.stderr)
            raise SystemExit(f"Kathara exec failed for node {node}")


def main() -> int:
    p = argparse.ArgumentParser(description="Start controlled DoS experiment inside Kathara nodes.")
    p.add_argument("yaml", type=Path)
    p.add_argument("--attackers", nargs="+")
    p.add_argument("--target")
    p.add_argument("--port", type=int)
    p.add_argument("--duration", type=int)
    p.add_argument("--workers", type=int)
    p.add_argument("--timeout", type=float)
    p.add_argument("--payload-size", type=int)
    p.add_argument("--delay-ms", type=int)
    p.add_argument("--mode", choices=["connect-flood", "hold-open"])
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    if shutil.which("kathara") is None and not args.dry_run:
        raise SystemExit("Kathara command not found in PATH")
    if not DOS_CLIENT_PATH.exists():
        raise SystemExit("dos_client.py must be in the same attacks/ directory")
    cfg = derive_config(load_yaml(args.yaml), args)
    print("Controlled DoS lab configuration:")
    print(json.dumps(cfg, indent=2))
    client_b64 = base64.b64encode(DOS_CLIENT_PATH.read_bytes()).decode("ascii")
    for node in cfg["attackers"]:
        inject_and_start(node, cfg, client_b64, args.dry_run)
    print("\nCollect logs with: python3 attacks/collect_dos_logs.py --attackers " + " ".join(cfg["attackers"]))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
