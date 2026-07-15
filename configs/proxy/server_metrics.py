#!/usr/bin/env python3
"""Thread-safe CSV metrics for the educational TCP service."""

from __future__ import annotations

import csv
import os
import threading
import time
from datetime import datetime
from pathlib import Path


FIELDNAMES = [
    "timestamp",
    "scenario",
    "event",
    "uptime_seconds",
    "active_connections",
    "accepted_connections",
    "rejected_connections",
    "busy_responses",
    "completed_connections",
    "max_simultaneous_connections",
    "average_connection_duration_ms",
    "active_threads",
]


class ServerMetrics:
    def __init__(
        self,
        path: str | Path | None = None,
        scenario_path: str | Path | None = None,
        interval: float | None = None,
    ) -> None:
        self.path = Path(path or os.getenv("SERVICE_METRICS_PATH", "/shared/server_metrics.csv"))
        self.scenario_path = Path(
            scenario_path
            or os.getenv("SERVICE_METRICS_SCENARIO_PATH", "/shared/server_metrics_scenario")
        )
        self.interval = interval if interval is not None else float(os.getenv("SERVICE_METRICS_INTERVAL", "1"))
        self.started_at = time.monotonic()
        self.state_lock = threading.Lock()
        self.write_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.sampler_started = False
        self.write_error_reported = False

        self.active_connections = 0
        self.accepted_connections = 0
        self.rejected_connections = 0
        self.busy_responses = 0
        self.completed_connections = 0
        self.max_simultaneous_connections = 0
        self.total_connection_duration_seconds = 0.0

    def start(self) -> None:
        if self.sampler_started:
            return
        self.sampler_started = True
        self.record("server_start")
        threading.Thread(target=self._sample_loop, name="metrics-sampler", daemon=True).start()

    def accepted(self) -> int:
        with self.state_lock:
            self.accepted_connections += 1
            self.active_connections += 1
            self.max_simultaneous_connections = max(
                self.max_simultaneous_connections,
                self.active_connections,
            )
            current = self.active_connections
        self.record("accepted")
        return current

    def current_active(self) -> int:
        with self.state_lock:
            return self.active_connections

    def rejected(self, busy_sent: bool) -> None:
        with self.state_lock:
            self.rejected_connections += 1
            if busy_sent:
                self.busy_responses += 1
        self.record("busy" if busy_sent else "rejected")

    def closed(self, duration_seconds: float) -> int:
        with self.state_lock:
            self.active_connections = max(0, self.active_connections - 1)
            self.completed_connections += 1
            self.total_connection_duration_seconds += max(0.0, duration_seconds)
            current = self.active_connections
        self.record("closed")
        return current

    def snapshot(self, event: str) -> dict[str, object]:
        with self.state_lock:
            average_ms = (
                self.total_connection_duration_seconds / self.completed_connections * 1000
                if self.completed_connections
                else 0.0
            )
            snapshot = {
                "timestamp": datetime.now().astimezone().isoformat(timespec="milliseconds"),
                "scenario": self._scenario(),
                "event": event,
                "uptime_seconds": f"{time.monotonic() - self.started_at:.3f}",
                "active_connections": self.active_connections,
                "accepted_connections": self.accepted_connections,
                "rejected_connections": self.rejected_connections,
                "busy_responses": self.busy_responses,
                "completed_connections": self.completed_connections,
                "max_simultaneous_connections": self.max_simultaneous_connections,
                "average_connection_duration_ms": f"{average_ms:.3f}",
                "active_threads": threading.active_count(),
            }
        return snapshot

    def record(self, event: str) -> None:
        row = self.snapshot(event)
        try:
            with self.write_lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                write_header = not self.path.exists() or self.path.stat().st_size == 0
                with self.path.open("a", newline="", encoding="utf-8") as output:
                    writer = csv.DictWriter(output, fieldnames=FIELDNAMES)
                    if write_header:
                        writer.writeheader()
                    writer.writerow(row)
                    output.flush()
        except OSError as exc:
            if not self.write_error_reported:
                print(f"[METRICS] Unable to write {self.path}: {exc}", flush=True)
                self.write_error_reported = True

    def _scenario(self) -> str:
        try:
            value = self.scenario_path.read_text(encoding="utf-8").strip()
            return value or "unknown"
        except OSError:
            return "unknown"

    def _sample_loop(self) -> None:
        while not self.stop_event.wait(max(0.1, self.interval)):
            self.record("sample")
