#!/usr/bin/env python3
"""Contract tests for the non-interactive assessment CLI."""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "bin" / "eks-assessment.sh"


def run_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), *arguments],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )


class HeadlessCliTests(unittest.TestCase):
    def test_help_documents_headless_commands_without_runtime_dependencies(self) -> None:
        result = run_cli("--help")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("collect --phase before|after --change-id ID", result.stdout)
        self.assertIn("release-gate --collection ID", result.stdout)
        self.assertIn("regression-gate --before ID --after ID", result.stdout)
        self.assertIn("Os subcomandos nunca solicitam input", result.stdout)

    def test_list_is_machine_friendly_and_does_not_require_kubectl(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "eks-20260102-after").mkdir()
            (root / "eks-20260101-before").mkdir()
            (root / "not-a-collection").mkdir()
            result = run_cli("list", "--root", temporary)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            ["eks-20260101-before", "eks-20260102-after"],
            result.stdout.splitlines(),
        )

    def test_collect_rejects_missing_or_invalid_required_options_before_preflight(self) -> None:
        missing = run_cli("collect", "--phase", "before")
        self.assertEqual(2, missing.returncode)
        self.assertIn("--change-id", missing.stderr)

        invalid_namespace = run_cli(
            "collect",
            "--phase",
            "before",
            "--change-id",
            "deploy-42",
            "--namespace",
            "INVALID_NAMESPACE",
        )
        self.assertEqual(2, invalid_namespace.returncode)
        self.assertIn("namespace inválido", invalid_namespace.stderr)

    def test_gate_arguments_are_explicit(self) -> None:
        release = run_cli("release-gate", "--collection", "sample")
        self.assertEqual(2, release.returncode)
        self.assertIn("provider inválido", release.stderr)

        regression = run_cli("regression-gate", "--before", "same", "--after", "same")
        self.assertEqual(2, regression.returncode)
        self.assertIn("devem ser diferentes", regression.stderr)

        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('if ((NON_INTERACTIVE == 1)); then profile="$default_profile"', source)

    def test_invalid_port_and_duration_return_usage_error(self) -> None:
        port = run_cli("dashboard", "--port", "70000")
        self.assertEqual(2, port.returncode)
        self.assertIn("porta inválida", port.stderr)

        duration = run_cli(
            "collect",
            "--phase",
            "after",
            "--change-id",
            "deploy-42",
            "--max-duration",
            "not-a-number",
        )
        self.assertEqual(2, duration.returncode)
        self.assertIn("deve ser numérico", duration.stderr)

    def test_collection_ids_reject_parent_directory_aliases(self) -> None:
        result = run_cli("terminal", "--collection", "..")
        self.assertEqual(2, result.returncode)
        self.assertIn("collection válido", result.stderr)


if __name__ == "__main__":
    unittest.main()
