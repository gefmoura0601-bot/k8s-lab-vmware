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


if __name__ == "__main__":
    unittest.main()
