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
    def test_progress_tracks_completed_components(self) -> None:
        supervisor = CollectionSupervisor()
        supervisor.start("progress-test", 60, ["one", "two"])
        initial = supervisor.status()
        self.assertEqual(0, initial["progressPercent"])
        self.assertEqual(2, len(initial["plannedComponents"]))
        result = supervisor.run("one", [sys.executable, "-c", "print('ok')"], timeout=5)
        self.assertEqual(0, result.returncode)
        self.assertEqual(50, supervisor.status()["progressPercent"])
        finished = supervisor.finish("COMPLETED")
        self.assertEqual(100, supervisor.status()["progressPercent"])
        self.assertGreaterEqual(finished["durationSeconds"], 0)
        self.assertIn("one", finished["componentDurationsSeconds"])

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
        self.assertIn("assessment_dashboard_pid", menu)
        self.assertIn("stop_assessment_dashboard", menu)
        self.assertIn("next_dashboard_port", menu)
        self.assertIn("Usar o dashboard atual", menu)
        self.assertIn("não foi identificado como dashboard do assessment e não será encerrado", menu)
        self.assertIn('assessment_dashboard_pid | grep -qx "$pid"', menu)
        self.assertIn("dashboard_host_rows", menu)
        self.assertIn("SSH_CONNECTION", menu)
        self.assertIn("DASHBOARD_PUBLIC_HOST inválido", menu)
        self.assertIn("Local neste host", menu)
        self.assertIn("vxlan.*", menu)
        self.assertIn("print_dashboard_urls", menu)
        self.assertNotIn("hostname -I 2>/dev/null | awk '{print $1}'", menu)
        self.assertIn("Abrir dashboard web nesta sessão", menu)
        self.assertNotIn("dashboard-$PORT.pid", menu)
        self.assertNotIn("nohup setsid", menu)
        self.assertIn("DASHBOARD_FOREGROUND", menu)
        self.assertIn('[[ "$op" == 0 || "$op" == 5 ]]', menu)
        self.assertIn('"$TOOL_ROOT/web/public"', menu)
        self.assertIn("KUBERNETES ASSESSMENT CONSOLE", menu)
        self.assertIn("render_menu", menu)
        self.assertIn("NO_COLOR", menu)

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

    def test_runtime_has_no_lab_path_or_master_node_dependency(self) -> None:
        root = Path(__file__).resolve().parents[1]
        runtime_files = list((root / "src").glob("*.py")) + list((root / "src").glob("*.sh")) + list((root / "bin").glob("*.sh"))
        for path in runtime_files:
            content = path.read_text(encoding="utf-8")
            self.assertNotIn("/workspace", content, path)
            self.assertNotIn("k8s-master", content, path)

    def test_help_and_version_are_processed_before_dependencies(self) -> None:
        menu = (Path(__file__).resolve().parents[1] / "bin" / "eks-assessment.sh").read_text(encoding="utf-8")
        self.assertLess(menu.index('-h|--help) usage; exit 0'), menu.index("need kubectl"))
        self.assertIn('--version)', menu)
        self.assertIn('--host 0.0.0.0', menu)
        self.assertIn('--allow-remote', menu)
        self.assertIn('--access-token', menu)


if __name__ == "__main__":
    unittest.main()
