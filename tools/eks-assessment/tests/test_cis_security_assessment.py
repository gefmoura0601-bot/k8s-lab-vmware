#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cis_security_assessment import assess


def available(*keys: str) -> dict:
    return {"resources": {key: {"state": "AVAILABLE"} for key in keys}}


class CisSecurityAssessmentTests(unittest.TestCase):
    def fixture(self, root: Path, context: str = "generic") -> tuple[dict, dict, dict]:
        (root / "metadata.json").write_text(json.dumps({"context": context, "clusterName": context}), encoding="utf-8")
        raw = {
            "roles": {"items": []}, "clusterroles": {"items": []}, "clusterrolebindings": {"items": []},
            "networkpolicies": {"items": [{"metadata": {"namespace": "payments", "name": "default-deny"}}]},
        }
        base = {"nodes": {"items": []}, "pods": {"items": []}, "workloads": {"items": [{
            "kind": "Deployment", "metadata": {"namespace": "payments", "name": "api"},
            "spec": {"template": {"spec": {"serviceAccountName": "api", "automountServiceAccountToken": False,
                "securityContext": {"runAsNonRoot": True, "seccompProfile": {"type": "RuntimeDefault"}},
                "containers": [{"name": "api", "securityContext": {"runAsNonRoot": True, "seccompProfile": {"type": "RuntimeDefault"}}}]}}},
        }]}}
        collection = available("roles", "clusterroles", "clusterrolebindings", "networkpolicies")
        return raw, base, collection

    def test_eks_control_plane_is_managed_and_not_scored(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); raw, base, collection = self.fixture(root, "arn:aws:eks:us-east-1:000000000000:cluster/test")
            report = assess(raw, base, collection, root, {"state": "AVAILABLE"})
        managed = [item for item in report["controls"] if item["applicability"] == "MANAGED_PROVIDER"]
        self.assertEqual("AWS", report["platform"])
        self.assertEqual(2, len(managed))
        self.assertTrue(all(item["managedResponsibility"] == "CLOUD_PROVIDER" for item in managed))
        self.assertEqual(8, report["summary"]["scored"])

    def test_self_managed_control_plane_never_passes_without_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); raw, base, collection = self.fixture(root)
            base["nodes"]["items"] = [{"metadata": {"labels": {"node-role.kubernetes.io/control-plane": ""}}}]
            report = assess(raw, base, collection, root)
        controls = [item for item in report["controls"] if item["evidenceSource"] == "ControlPlaneEvidence"]
        self.assertEqual("SELF_MANAGED", report["platform"])
        self.assertEqual({"EVIDENCE_UNAVAILABLE"}, {item["applicability"] for item in controls})
        self.assertNotIn("PASS", {item["status"] for item in controls})

    def test_generic_cluster_uses_only_universal_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); raw, base, collection = self.fixture(root)
            report = assess(raw, base, collection, root)
        self.assertEqual("GENERIC", report["platform"])
        self.assertFalse(any("aws" in item["controlId"] for item in report["controls"]))
        self.assertEqual(100, report["summary"]["scorePercent"])

    def test_risky_workload_and_wildcard_rbac_warn(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); raw, base, collection = self.fixture(root)
            raw["roles"]["items"] = [{"kind": "Role", "metadata": {"namespace": "payments", "name": "wide"}, "rules": [{"verbs": ["*"], "resources": ["pods"]}]}]
            pod_spec = base["workloads"]["items"][0]["spec"]["template"]["spec"]
            pod_spec["containers"][0]["securityContext"]["privileged"] = True
            report = assess(raw, base, collection, root)
        statuses = {item["controlId"]: item["status"] for item in report["controls"]}
        self.assertEqual("WARN", statuses["cis.k8s.rbac.wildcards"])
        self.assertEqual("WARN", statuses["cis.k8s.pod.privileged"])


if __name__ == "__main__":
    unittest.main()
