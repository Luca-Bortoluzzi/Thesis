import tempfile
import unittest
from pathlib import Path

import show_connection_results as report


class ReportTests(unittest.TestCase):
    def test_current_schema_reports_availability_timeouts_and_duration(self):
        rows = [
            {
                "timestamp": "2026-07-14T10:00:00.100+02:00",
                "experiment_elapsed_ms": "100.0",
                "client": "pc_a1",
                "status": "OK",
                "icmp_status": "OK",
                "icmp_rtt_ms": "1.0",
                "icmp_error": "",
                "tcp_status": "OK",
                "tcp_connect_ms": "2.0",
                "tcp_error": "",
                "application_status": "OK",
                "application_response_ms": "3.0",
                "request_completion_ms": "5.0",
                "error": "",
            },
            {
                "timestamp": "2026-07-14T10:00:01.100+02:00",
                "experiment_elapsed_ms": "1100.0",
                "client": "pc_a1",
                "status": "FAIL",
                "icmp_status": "FAIL",
                "icmp_rtt_ms": "",
                "icmp_error": "icmp_timeout",
                "tcp_status": "OK",
                "tcp_connect_ms": "2.5",
                "tcp_error": "",
                "application_status": "FAIL",
                "application_response_ms": "",
                "request_completion_ms": "",
                "error": "application_timeout",
            },
            {
                "timestamp": "2026-07-14T10:00:02.500+02:00",
                "experiment_elapsed_ms": "2500.0",
                "client": "pc_a2",
                "status": "FAIL",
                "icmp_status": "OK",
                "icmp_rtt_ms": "1.5",
                "icmp_error": "",
                "tcp_status": "FAIL",
                "tcp_connect_ms": "",
                "tcp_error": "tcp_connect_timeout",
                "application_status": "FAIL",
                "application_response_ms": "",
                "request_completion_ms": "",
                "error": "tcp_connect_timeout",
            },
        ]

        metrics, clients, errors = report.build_report(rows)
        metric_map = dict(metrics)

        self.assertEqual(metric_map["Application availability"], "33.33%")
        self.assertEqual(metric_map["Requests with TCP/app timeout"], 2)
        self.assertEqual(metric_map["TCP connection timeouts"], 1)
        self.assertEqual(metric_map["Application response timeouts"], 1)
        self.assertEqual(metric_map["ICMP timeouts"], 1)
        self.assertEqual(metric_map["Effective experiment duration"], "2.500 s")
        self.assertEqual(clients["pc_a1"]["availability"], "50.00%")
        self.assertEqual(clients["pc_a1"]["min_ms"], "3.00")
        self.assertEqual(clients["pc_a2"]["avg_ms"], "N/A")
        self.assertEqual(errors["ICMP: icmp_timeout"], 1)

    def test_legacy_csv_is_still_supported(self):
        rows = [
            {
                "timestamp": "2026-07-14T10:00:00+02:00",
                "client": "pc_a1",
                "status": "OK",
                "elapsed_ms": "80.0",
                "error": "",
            },
            {
                "timestamp": "2026-07-14T10:00:01+02:00",
                "client": "pc_a1",
                "status": "FAIL",
                "elapsed_ms": "",
                "error": "tcp_connection_failed_or_timeout",
            },
        ]

        metrics, clients, errors = report.build_report(rows)

        self.assertEqual(dict(metrics)["Application availability"], "50.00%")
        self.assertEqual(clients["pc_a1"]["avg_ms"], "80.00")
        self.assertEqual(errors["tcp_connection_failed_or_timeout"], 1)

    def test_new_csv_round_trip(self):
        content = (
            "timestamp,scenario,client,request_index,status,application_status,application_response_ms\n"
            "2026-07-14T10:00:00+02:00,baseline,pc_a1,1,OK,OK,2.5\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            csv_path = Path(directory) / "results.csv"
            csv_path.write_text(content, encoding="utf-8")
            rows = report.load_rows(csv_path)

        self.assertTrue(report.is_current_schema(rows))
        self.assertEqual(report.metric_values(rows, "application_response_ms"), [2.5])


if __name__ == "__main__":
    unittest.main()
