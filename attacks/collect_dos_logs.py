#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import List


def kathara_read(node: str, remote_file: str) -> str:
    cmd = ["kathara", "exec", node, "--", "bash", "-lc", f"cat {remote_file} 2>/dev/null || true"]
    result = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())
    return result.stdout.strip()


def main() -> int:
    p = argparse.ArgumentParser(description="Collect controlled DoS JSON logs from Kathara attacker nodes.")
    p.add_argument("--attackers", nargs="+", default=["pc_e", "pc_f"])
    p.add_argument("--out", type=Path, default=Path("results/dos_summary.json"))
    args = p.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    collected: List[dict] = []
    for node in args.attackers:
        remote = f"/tmp/kathara_dos/dos_client_{node}.json"
        content = kathara_read(node, remote)
        if not content:
            collected.append({"node": node, "status": "missing_log", "remote_file": remote})
            continue
        try:
            data = json.loads(content)
            data["node"] = node
            data["status"] = "ok"
            collected.append(data)
        except json.JSONDecodeError:
            collected.append({"node": node, "status": "invalid_json", "raw": content[:1000]})
    total_attempts = sum(item.get("total_attempts", 0) for item in collected)
    total_ok = sum(item.get("total_ok", 0) for item in collected)
    total_failed = sum(item.get("total_failed", 0) for item in collected)
    summary = {
        "attackers": args.attackers,
        "total_attempts": total_attempts,
        "total_ok": total_ok,
        "total_failed": total_failed,
        "success_rate": (total_ok / total_attempts) if total_attempts else 0.0,
        "nodes": collected,
    }
    args.out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"Saved: {args.out}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
