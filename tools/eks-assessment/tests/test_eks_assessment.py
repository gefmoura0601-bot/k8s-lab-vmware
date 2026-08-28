#!/usr/bin/env python3
"""Regression tests for the adaptive EKS/Kubernetes assessment."""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import assessment_dashboard as dashboard
import aws_eks_assessment as aws_assessment
import eks_comprehensive_assessment as comprehensive
import eks_semantic_assessment as semantic


class AssessmentRegressionTests(unittest.TestCase):
    def assessment(self, directory: Path) -> comprehensive.Assessment:
        return comprehensive.Assessment(directory, {}, {"state": "AVAILABLE", "resources": {}})

    def test_fingerprint_is_stable_when_evidence_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            first = self.assessment(Path(temporary))
            second = self.assessment(Path(temporary))
            first.add(
                "WARN", "Reliability", "Replica count", "replicas=1",
                namespace="payments", workload="Deployment/api",
                rule_id="k8s.workload.replicas",
            )
            second.add(
                "CRIT", "Reliability", "Replica count", "replicas=0",
                namespace="payments", workload="Deployment/api",
                rule_id="k8s.workload.replicas",
            )
        self.assertEqual(first.findings[0]["fingerprint"], second.findings[0]["fingerprint"])
        self.assertNotEqual(first.findings[0]["evidenceHash"], second.findings[0]["evidenceHash"])

    def test_empty_evidence_is_not_reported_as_compliant(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            assessment = self.assessment(Path(temporary))
            result = semantic.apply_semantic_assessment(assessment)
        self.assertGreater(result["checksAdded"], 0)
        severities = {item["severity"] for item in assessment.findings}
        self.assertTrue({"UNKNOWN", "PARTIAL", "N/A"} & severities)

    def test_api_budget_stops_collection_deterministically(self) -> None:
        budget = comprehensive.ApiBudget(10, 60, 1024, 0)
        for _ in range(10):
            budget.before_request()
        with self.assertRaises(comprehensive.CollectionBudgetExceeded):
            budget.before_request()
        self.assertEqual(budget.summary()["state"], "PARTIAL")

    def test_namespaced_universal_inventory_does_not_shadow_scope(self) -> None:
        response = subprocess.CompletedProcess(
            ["kubectl"], 0, "team-a object-a Example example.io/v1", ""
        )
        with tempfile.TemporaryDirectory() as temporary, patch.object(
            comprehensive, "run_kubectl", return_value=response
        ) as mocked:
            result = comprehensive.collect_universal_inventory(
                Path(temporary),
                {"examples.example.io"},
                set(),
                {},
                {},
                30,
                200,
                1,
                comprehensive.ApiBudget(10, 60, 1024 * 1024, 0),
                0,
                "team-a",
                False,
            )
        self.assertEqual(result["objectCount"], 1)
        command = mocked.call_args.args[0]
        self.assertIn("-n", command)
        self.assertIn("team-a", command)

    def test_sensitive_resources_are_not_requested(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.object(
            comprehensive, "run_kubectl"
        ) as mocked:
            result = comprehensive.collect_universal_inventory(
                Path(temporary),
                {"secrets", "configmaps"},
                set(),
                {},
                {},
                30,
                200,
                1,
                comprehensive.ApiBudget(10, 60, 1024 * 1024, 0),
                0,
            )
        mocked.assert_not_called()
        self.assertEqual("PARTIAL", result["state"])
        self.assertNotIn("secrets", comprehensive.RESOURCE_SPECS)

    def test_api_discovery_failure_never_proves_not_applicable(self) -> None:
        failed = subprocess.CompletedProcess(["kubectl"], 1, "", "Forbidden")
        with tempfile.TemporaryDirectory() as temporary, patch.object(
            comprehensive, "run_kubectl", return_value=failed
        ), patch.object(comprehensive, "write_collection_provenance"):
            _raw, collection = comprehensive.collect_live(
                Path(temporary), 5, 50, 1,
                comprehensive.ApiBudget(1000, 60, 1024 * 1024, 0),
                0,
            )
        states = {entry["state"] for entry in collection["resources"].values()}
        self.assertEqual({"PARTIAL"}, states)

    def test_snapshot_sanitization_removes_arbitrary_values(self) -> None:
        source = {
            "items": [{
                "metadata": {"name": "api", "uid": "hidden"},
                "spec": {"containers": [{"env": [
                    {"name": "PUBLIC_SETTING", "value": "must-not-persist"},
                    {"name": "JAVA_TOOL_OPTIONS", "value": "-XX:+UseG1GC"},
                ]}]},
            }]
        }
        rendered = str(comprehensive.sanitize_snapshot_tree(source))
        self.assertNotIn("must-not-persist", rendered)
        self.assertNotIn("hidden", rendered)
        self.assertIn("-XX:+UseG1GC", rendered)

    def test_resume_requires_matching_identity_and_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "pods.json").write_text('{"items":[]}', encoding="utf-8")
            identity = {"context": "ctx", "serverHash": "server", "namespaceScope": "team-a"}
            with patch.object(comprehensive, "collection_identity", return_value=identity):
                comprehensive.write_collection_provenance(root, "team-a")
                valid, _reason = comprehensive.valid_resume_provenance(root, "team-a")
                self.assertTrue(valid)
                (root / "pods.json").write_text('{"items":[{}]}', encoding="utf-8")
                valid, reason = comprehensive.valid_resume_provenance(root, "team-a")
                self.assertFalse(valid)
                self.assertIn("integrity mismatch", reason)

    def test_non_eks_environment_is_explicitly_not_applicable(self) -> None:
        collector = aws_assessment.AwsCollector("", "", 5, 0, 0, 10, False)
        result = collector.result()
        self.assertEqual(result["state"], "N/A")
        self.assertEqual(result["findings"][0]["severity"], "N/A")

    def test_aws_evidence_redacts_account_and_credentials(self) -> None:
        value = aws_assessment.sanitize(
            {
                "arn": "arn:aws:eks:us-east-1:123456789012:cluster/example",
                "secretKey": "do-not-persist",
                "message": "access_key=ABC123",
            }
        )
        rendered = str(value)
        self.assertNotIn("123456789012", rendered)
        self.assertNotIn("do-not-persist", rendered)
        self.assertNotIn("ABC123", rendered)

    def test_dashboard_does_not_call_generic_context_an_eks_cluster(self) -> None:
        generic_view = subprocess.CompletedProcess(
            ["kubectl"], 0,
            '{"contexts":[{"context":{"cluster":"kubernetes"}}]}', "",
        )
        with patch.object(
            dashboard,
            "run",
            side_effect=[
                subprocess.CompletedProcess(["kubectl"], 0, "kubernetes-admin@kubernetes", ""),
                generic_view,
            ],
        ):
            self.assertEqual(dashboard.eks_cluster_name(), "")

    def test_dashboard_extracts_eks_name_from_arn(self) -> None:
        eks_view = subprocess.CompletedProcess(
            ["kubectl"], 0,
            '{"contexts":[{"context":{"cluster":"arn:aws:eks:us-east-1:123456789012:cluster/prod-blue"}}]}',
            "",
        )
        with patch.object(
            dashboard,
            "run",
            side_effect=[
                subprocess.CompletedProcess(["kubectl"], 0, "context", ""),
                eks_view,
            ],
        ):
            self.assertEqual(dashboard.eks_cluster_name(), "prod-blue")

    def test_dashboard_and_menu_are_loopback_only(self) -> None:
        root = Path(__file__).resolve().parents[1]
        dashboard_source = (root / "src" / "assessment_dashboard.py").read_text(encoding="utf-8")
        menu_source = (root / "bin" / "eks-assessment.sh").read_text(encoding="utf-8")
        self.assertIn('default="127.0.0.1"', dashboard_source)
        self.assertNotIn("--host 0.0.0.0", menu_source)


if __name__ == "__main__":
    unittest.main()
