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
            self.assertIn("nodeHealth", value)
            self.assertTrue(value["manifestQuality"]["findings"])
            self.assertTrue(value["bestPractices"]["rules"])
            self.assertEqual(value["logs"]["state"], "DISABLED")
            self.assertTrue((root / "operational-insights.json").is_file())

    def test_node_health_decomposes_metrics_without_claiming_process_precision(self) -> None:
        node = {
            "metadata": {"name": "node-a"},
            "status": {
                "capacity": {"cpu": "4", "memory": "8Gi", "pods": "110"},
                "allocatable": {"cpu": "3500m", "memory": "7Gi", "pods": "100"},
                "conditions": [
                    {"type": "Ready", "status": "True"},
                    {"type": "MemoryPressure", "status": "False"},
                    {"type": "DiskPressure", "status": "False"},
                    {"type": "PIDPressure", "status": "False"},
                ],
                "nodeInfo": {"containerRuntimeVersion": "containerd://2.1", "osImage": "Linux"},
            },
        }

        def pod(namespace: str, name: str, owner_kind: str, cpu: str, memory: str) -> dict:
            return {
                "metadata": {"namespace": namespace, "name": name, "ownerReferences": [{"kind": owner_kind, "name": name}]},
                "spec": {"nodeName": "node-a", "containers": [{"name": "main", "resources": {"requests": {"cpu": cpu, "memory": memory}}}]},
                "status": {"phase": "Running"},
            }

        pods = [
            pod("kube-system", "cni", "DaemonSet", "100m", "128Mi"),
            pod("kube-system", "dns", "ReplicaSet", "100m", "128Mi"),
            pod("apps", "api", "ReplicaSet", "500m", "512Mi"),
        ]
        node_metrics = [{"metadata": {"name": "node-a"}, "timestamp": "2026-08-31T00:00:00Z", "window": "30s", "usage": {"cpu": "1", "memory": "2Gi"}}]
        pod_metrics = [
            {"metadata": {"namespace": "kube-system", "name": "cni"}, "containers": [{"name": "main", "usage": {"cpu": "100m", "memory": "128Mi"}}]},
            {"metadata": {"namespace": "kube-system", "name": "dns"}, "containers": [{"name": "main", "usage": {"cpu": "100m", "memory": "128Mi"}}]},
            {"metadata": {"namespace": "apps", "name": "api"}, "containers": [{"name": "main", "usage": {"cpu": "400m", "memory": "512Mi"}}]},
        ]
        value = insights.node_health([node], pods, node_metrics, pod_metrics)
        item = value["items"][0]
        breakdown = item["usage"]["breakdown"]
        self.assertEqual(value["state"], "PASS")
        self.assertAlmostEqual(breakdown["daemonSets"]["cpuCores"], 0.1)
        self.assertAlmostEqual(breakdown["kubernetesPods"]["cpuCores"], 0.1)
        self.assertAlmostEqual(breakdown["workloads"]["cpuCores"], 0.4)
        self.assertAlmostEqual(breakdown["nodeOverheadUnattributed"]["cpuCores"], 0.4)
        self.assertEqual(item["evidence"]["podMetricsCoveragePercent"], 100.0)
        self.assertIn("não atribuído", value["notice"])

    def test_container_image_lifecycle_uses_evidence_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            value = insights.versions(
                Path(temporary), [],
                [{"containers": [{"image": "registry.example/api:43a7abb"}]}],
                [], "generic-kubernetes", {},
            )
        row = next(item for item in value["items"] if item["component"] == "Container image")
        self.assertEqual(row["version"], "43a7abb")
        self.assertEqual(row["state"], "DETECTED")
        self.assertEqual(row["supportState"], "EVIDENCE_UNAVAILABLE")
        self.assertEqual(row["supportUntil"], "N/A")
        self.assertIn("lifecycle", row["lifecycleReason"])

    def test_missing_ready_condition_is_partial_not_critical(self) -> None:
        value = insights.node_health(
            [{"metadata": {"name": "node-a"}, "status": {"capacity": {"cpu": "2", "memory": "2Gi", "pods": "50"}, "allocatable": {"cpu": "2", "memory": "2Gi", "pods": "50"}}}],
            [], [], [],
        )
        self.assertEqual(value["state"], "PARTIAL")
        self.assertIsNone(value["items"][0]["ready"])
        self.assertIn("Condição Ready indisponível", value["items"][0]["diagnosis"])

    def test_pod_requests_include_restartable_init_and_overhead(self) -> None:
        value = insights.pod_requests({"spec": {
            "containers": [{"resources": {"requests": {"cpu": "100m", "memory": "100Mi"}}}],
            "initContainers": [
                {"restartPolicy": "Always", "resources": {"requests": {"cpu": "50m", "memory": "50Mi"}}},
                {"resources": {"requests": {"cpu": "300m", "memory": "300Mi"}}},
            ],
            "overhead": {"cpu": "10m", "memory": "10Mi"},
        }})
        self.assertAlmostEqual(value["cpuCores"], 0.36)
        self.assertAlmostEqual(value["memoryBytes"], 360 * 2**20)

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
