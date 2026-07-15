#!/usr/bin/env python3

import csv
import tempfile
import unittest
from pathlib import Path
import sys
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from run_connection_tests import (  # noqa: E402
    TestConfig,
    build_output_layout,
    client_log_path,
    next_simulation_number,
    run_tests,
)


class OutputLayoutTests(unittest.TestCase):
    def make_config(self, lab_dir: Path, simulation_number: int | None = None) -> TestConfig:
        return TestConfig(
            target_input="server",
            target_resolved="10.0.0.10",
            port=9000,
            clients=["pc_a1"],
            scenario="baseline",
            attempts=1,
            timeout=1,
            delay=0,
            lab_dir=lab_dir,
            simulation_number=simulation_number,
        )

    def test_next_number_is_scoped_to_lab(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "dos_lab" / "simulation_0002").mkdir(parents=True)
            (root / "other_lab" / "simulation_0010").mkdir(parents=True)

            self.assertEqual(next_simulation_number(root, "dos_lab"), 3)

    def test_layout_separates_lab_simulation_and_host(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            config = self.make_config(output_dir / "labs" / "dos_lab", 7)

            layout = build_output_layout(config, output_dir)

            self.assertEqual(
                layout.results_dir,
                output_dir / "results" / "dos_lab" / "simulation_0007",
            )
            self.assertEqual(
                layout.main_logs_dir,
                output_dir / "logs" / "dos_lab" / "simulation_0007",
            )
            self.assertEqual(
                client_log_path(layout, "pc_a1", "baseline"),
                output_dir / "logs" / "dos_lab" / "pc_a1" / "simulation_0007" / "baseline.log",
            )

    @patch("run_connection_tests.list_docker_containers")
    @patch("run_connection_tests.run_container_measurements")
    def test_run_writes_csv_and_logs_to_layout(self, measurements, containers) -> None:
        containers.return_value = ["kathara_test_pc_a1_container"]
        measurements.return_value = (
            {
                "icmp_status": "OK",
                "icmp_rtt_ms": 1.2,
                "icmp_error": "",
                "tcp_status": "OK",
                "tcp_connect_ms": 2.3,
                "tcp_error": "",
                "application_status": "OK",
                "application_response_ms": 3.4,
                "request_completion_ms": 5.7,
                "application_error": "",
            },
            "measurement-debug",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            config = self.make_config(output_dir / "labs" / "dos_lab", 4)

            csv_path = run_tests(config, output_dir)

            self.assertEqual(
                csv_path,
                output_dir / "results" / "dos_lab" / "simulation_0004" / "connection_results_baseline.csv",
            )
            with csv_path.open(newline="", encoding="utf-8") as csv_file:
                row = next(csv.DictReader(csv_file))
            self.assertEqual(row["simulation"], "simulation_0004")
            self.assertTrue(
                (output_dir / "logs" / "dos_lab" / "simulation_0004" / "connection_tests_baseline.log").is_file()
            )
            self.assertTrue(
                (output_dir / "logs" / "dos_lab" / "pc_a1" / "simulation_0004" / "baseline.log").is_file()
            )


if __name__ == "__main__":
    unittest.main()
