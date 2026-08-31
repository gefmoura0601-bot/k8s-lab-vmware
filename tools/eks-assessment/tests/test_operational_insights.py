"""Regression tests for operational insights and optional sanitized logs."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import operational_insights as insights


class OperationalInsightsTests(unittest.TestCase):
    def test_generates_all_domains_from_sanitized_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "nodes.json").write_text(json.dumps({"items": [{"metadata": {"name": "n1", "labels": {}}, "spec": {"providerID": "aws:///us-east-1a/i-sanitized"}, "status": {"nodeInfo": {"kubeletVersion": "v1.35.0", "containerRuntimeVersion": "containerd://2", "osImage": "Linux", "kernelVersion": "6"}}}]}))
            (root / "pods.json").write_text(json.dumps({"items": [{"metadata": {"namespace": "apps", "name": "api-1"}, "spec": {"nodeName": "n1"}, "status": {"phase": "Pending", "containerStatuses": [{"restartCount": 2, "state": {"waiting": {"reason": "ImagePullBackOff"}}}]}}]}))
            (root / "events.json").write_text(json.dumps({"items": [{"metadata": {"namespace": "apps"}, "type": "Warning", "reason": "FailedScheduling", "regarding": {"kind": "Pod", "name": "api-1"}, "count": 3}]}))
            workloads = [{"namespace": "apps", "ref": "Deployment/api", "containers": [{"name": "api", "image": "repo/api:1.2.3"}]}]
            findings = [{"severity": "WARN", "category": "Reliability", "ruleId": "k8s.workload.replicas", "resourceKey": "apps|Deployment/api|-", "namespace": "apps", "workload": "Deployment/api", "container": "-", "check": "Replicas", "detail": "replicas=1", "recommendation": "Use múltiplas réplicas.", "applicability": "APPLICABLE"}]
            value = insights.generate(root, workloads, findings, [], [], {"state": "AVAILABLE"})
            self.assertEqual(value["platform"], "eks")
            self.assertEqual(value["diagnostics"]["summary"]["warnings"], 1)
            self.assertTrue(value["versions"]["items"])
            self.assertTrue(value["manifestQuality"]["findings"])
            self.assertTrue(value["bestPractices"]["rules"])
            self.assertEqual(value["logs"]["state"], "DISABLED")
            self.assertTrue((root / "operational-insights.json").is_file())

    def test_provider_rules_are_not_applied_to_another_provider(self) -> None:
        rows = insights.best_practices("aks", [], [])["rules"]
        eks = [x for x in rows if x["provider"] == "eks"]
        aks = [x for x in rows if x["provider"] == "aks"]
        self.assertTrue(all(x["applicability"] == "NOT_APPLICABLE" for x in eks))
        self.assertTrue(all(x["applicability"] == "MANUAL_REVIEW" for x in aks))
        self.assertFalse(any(x["status"] == "PASS" for x in aks))

    def test_logs_require_targets_and_redact_credentials(self) -> None:
        with patch.dict(os.environ, {"ASSESSMENT_INCLUDE_LOGS": "1", "ASSESSMENT_LOG_TARGETS": ""}, clear=False):
            self.assertEqual(insights.sanitized_logs()["state"], "REFUSED")
        completed = subprocess.CompletedProcess([], 0, 'token=abc Authorization: Bearer xyz\n{"password":"json-secret"}\nkey=AKIA1234567890ABCDEF\nhttps://user:pass@example.test\nnormal line', "")
        with patch.dict(os.environ, {"ASSESSMENT_INCLUDE_LOGS": "1", "ASSESSMENT_LOG_TARGETS": "apps/deployment/api:app"}, clear=False), patch.object(insights.subprocess, "run", return_value=completed):
            value = insights.sanitized_logs()
        content = value["entries"][0]["content"]
        self.assertNotIn("abc", content)
        self.assertNotIn("xyz", content)
        self.assertNotIn("json-secret", content)
        self.assertNotIn("AKIA1234567890ABCDEF", content)
        self.assertNotIn("user:pass@", content)
        self.assertIn("[REDACTED]", content)


if __name__ == "__main__":
    unittest.main()
