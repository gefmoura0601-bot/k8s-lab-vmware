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


if __name__ == "__main__":
    unittest.main()