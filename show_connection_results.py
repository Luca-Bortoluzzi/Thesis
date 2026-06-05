#!/usr/bin/env python3
"""
show_connection_results.py

Legge i risultati generati da run_connection_tests.py e mostra un report
sintetico. Non esegue test e non crea nuovi log.

Uso, dalla cartella del laboratorio:
    ./show_connection_results.py

Oppure indicando un CSV specifico:
    ./show_connection_results.py results/connection_results_baseline_20260605_112113.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean


def find_latest_csv(lab_dir: Path) -> Path | None:
    results_dir = lab_dir / "results"
    if not results_dir.exists():
        return None
    files = sorted(results_dir.glob("connection_results_*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def load_rows(csv_path: Path) -> list[dict[str, str]]:
    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)


def safe_float(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def print_metric_table(metrics: list[tuple[str, object]]) -> None:
    print("Metriche generali")
    print(f"{'Metrica':<35} {'Valore':<18}")
    print("-" * 54)
    for name, value in metrics:
        print(f"{name:<35} {str(value):<18}")


def print_client_table(client_stats: dict[str, dict[str, object]]) -> None:
    print("\nRiepilogo per client")
    print(f"{'Client':<15} {'Richieste':<10} {'OK':<8} {'Fail':<8} {'Media ms':<10}")
    print("-" * 58)
    for client in sorted(client_stats):
        s = client_stats[client]
        print(
            f"{client:<15} "
            f"{s['total']:<10} "
            f"{s['ok']:<8} "
            f"{s['fail']:<8} "
            f"{s['avg_ms']:<10}"
        )


def build_report(rows: list[dict[str, str]]) -> tuple[list[tuple[str, object]], dict[str, dict[str, object]], Counter]:
    total = len(rows)
    ok = sum(1 for row in rows if row.get("status") == "OK")
    fail = total - ok

    elapsed_values = [safe_float(row.get("elapsed_ms", "")) for row in rows]
    elapsed_values = [value for value in elapsed_values if value is not None]
    avg_ms = f"{mean(elapsed_values):.2f}" if elapsed_values else "N/D"

    errors = Counter(row.get("error", "") or "nessun_errore" for row in rows if row.get("status") != "OK")

    client_raw = defaultdict(lambda: {"total": 0, "ok": 0, "fail": 0, "elapsed": []})
    for row in rows:
        client = row.get("client", "N/D")
        client_raw[client]["total"] += 1
        if row.get("status") == "OK":
            client_raw[client]["ok"] += 1
        else:
            client_raw[client]["fail"] += 1
        value = safe_float(row.get("elapsed_ms", ""))
        if value is not None:
            client_raw[client]["elapsed"].append(value)

    client_stats: dict[str, dict[str, object]] = {}
    for client, data in client_raw.items():
        elapsed = data["elapsed"]
        avg = f"{mean(elapsed):.2f}" if elapsed else "N/D"
        client_stats[client] = {
            "total": data["total"],
            "ok": data["ok"],
            "fail": data["fail"],
            "avg_ms": avg,
        }

    metrics = [
        ("Richieste totali", total),
        ("Richieste completate", ok),
        ("Richieste fallite", fail),
        ("Tempo medio risposta ms", avg_ms),
        ("Errori client", fail),
    ]
    return metrics, client_stats, errors


def infer_context(rows: list[dict[str, str]]) -> dict[str, str]:
    first = rows[0] if rows else {}
    clients = sorted({row.get("client", "") for row in rows if row.get("client")})
    return {
        "scenario": first.get("scenario", "N/D"),
        "target": first.get("target", "N/D"),
        "port": first.get("port", "N/D"),
        "clients": ", ".join(clients) if clients else "N/D",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mostra il riepilogo dei risultati dei test nc.")
    parser.add_argument("csv_file", nargs="?", help="CSV da leggere. Se omesso, usa l'ultimo in results/.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    lab_dir = Path.cwd()

    csv_path = Path(args.csv_file) if args.csv_file else find_latest_csv(lab_dir)
    if csv_path is None or not csv_path.exists():
        print("[ERRORE] Nessun CSV trovato. Esegui prima ./run_connection_tests.py", file=sys.stderr)
        return 1

    rows = load_rows(csv_path)
    if not rows:
        print(f"[ERRORE] Il file CSV è vuoto: {csv_path}", file=sys.stderr)
        return 1

    context = infer_context(rows)
    metrics, client_stats, errors = build_report(rows)

    print(f"[INFO] File risultati: {csv_path}")
    print("")
    print("Report sintetico connessioni")
    print(f"Scenario: {context['scenario']}")
    print(f"Target: {context['target']}:{context['port']}")
    print(f"Client testati: {context['clients']}")
    print("")

    print_metric_table(metrics)
    print_client_table(client_stats)

    print("\nDettaglio errori client")
    if not errors:
        print("nessun errore rilevato")
    else:
        for error, count in errors.most_common():
            print(f"- {error}: {count}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
