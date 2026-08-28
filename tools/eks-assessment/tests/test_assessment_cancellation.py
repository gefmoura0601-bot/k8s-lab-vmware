#!/usr/bin/env python3
"""Regression tests for bounded and operator-cancelled assessment execution."""
from __future__ import annotations

import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from assessment_process_supervisor import CollectionSupervisor


class CollectionSupervisorTests(unittest.TestCase):
    def test_component_timeout_is_bounded(self) -> None:
        supervisor = CollectionSupervisor()
        supervisor.start("timeout-test", 60)
        started = time.monotonic()
        result = supervisor.run(
            "sleep",
            [sys.executable, "-c", "import time; time.sleep(30)"],
            timeout=0.15,
        )
        self.assertEqual(124, result.returncode)
        self.assertLess(time.monotonic() - started, 4)
        self.assertEqual("TIMED_OUT", supervisor.status()["stopKind"])
        supervisor.finish()

    def test_operator_cancel_stops_active_process(self) -> None:
        supervisor = CollectionSupervisor()
        supervisor.start("cancel-test", 60)
        result: list[subprocess.CompletedProcess[str]] = []
        worker = threading.Thread(
            target=lambda: result.append(
                supervisor.run(
                    "sleep",
                    [sys.executable, "-c", "import time; time.sleep(30)"],
                    timeout=30,
                )
            )
        )
        worker.start()
        for _ in range(100):
            if supervisor.status().get("pid"):
                break
            time.sleep(0.01)
        self.assertTrue(supervisor.cancel("unit-test Ctrl+C"))
        worker.join(timeout=4)
        self.assertFalse(worker.is_alive())
        self.assertEqual(130, result[0].returncode)
        self.assertEqual("CANCELLED", supervisor.status()["stopKind"])
        supervisor.finish()

    def test_menu_has_group_cleanup_and_total_budget(self) -> None:
        menu = (Path(__file__).resolve().parents[1] / "bin" / "eks-assessment.sh").read_text(encoding="utf-8")
        self.assertIn("trap cancel_on_signal INT TERM", menu)
        self.assertIn('kill -TERM -- "-$ACTIVE_PID"', menu)
        self.assertIn("ASSESSMENT_MAX_DURATION_SECONDS", menu)
        self.assertIn("--kill-after=10s", menu)
        self.assertIn("dashboard_port_in_use", menu)
        self.assertIn("Abrir dashboard web nesta sessão", menu)
        self.assertNotIn("dashboard-$PORT.pid", menu)
        self.assertNotIn("nohup setsid", menu)
        self.assertIn("DASHBOARD_FOREGROUND", menu)
        self.assertIn('[[ "$op" == 0 || "$op" == 5 ]]', menu)
        self.assertIn('"$TOOL_ROOT/web/public"', menu)

    def test_menu_uses_preflight_and_portable_python(self) -> None:
        root = Path(__file__).resolve().parents[1]
        menu = (root / "bin" / "eks-assessment.sh").read_text(encoding="utf-8")
        assessment = (root / "src" / "assess-eks.sh").read_text(encoding="utf-8")
        dashboard = (root / "src" / "assessment_dashboard.py").read_text(encoding="utf-8")
        preflight = (root / "src" / "assessment-preflight.sh").read_text(encoding="utf-8")
        self.assertIn("assessment-preflight.sh", menu)
        self.assertIn('"$PYTHON_BIN"', menu)
        self.assertNotIn("need python3.11", menu)
        self.assertIn('PROMETHEUS_NAMESPACE="${PROMETHEUS_NAMESPACE:-}"', assessment)
        self.assertIn('PROMETHEUS_SERVICE="${PROMETHEUS_SERVICE:-}"', assessment)
        self.assertNotIn('finding N/A eks "AWS/EKS collector"', assessment)
        self.assertNotIn('value="monitoring"', dashboard)
        self.assertNotIn('value="kube-prometheus-stack-prometheus"', dashboard)
        self.assertIn("auth can-i", preflight)
        self.assertNotIn("kubectl apply", preflight)
        self.assertNotIn("kubectl patch", preflight)
        self.assertNotIn("kubectl delete", preflight)

    def test_help_and_version_are_processed_before_dependencies(self) -> None:
        menu = (Path(__file__).resolve().parents[1] / "bin" / "eks-assessment.sh").read_text(encoding="utf-8")
        self.assertLess(menu.index('-h|--help) usage; exit 0'), menu.index("need kubectl"))
        self.assertIn('--version)', menu)
        self.assertIn('--host 0.0.0.0', menu)
        self.assertIn('--allow-remote', menu)


if __name__ == "__main__":
    unittest.main()
