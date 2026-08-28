#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cis_security_assessment import assess, compare_reports


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
                "containers": [{"name": "api", "image": "example/api@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "securityContext": {"runAsNonRoot": True, "seccompProfile": {"type": "RuntimeDefault"}, "allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True, "capabilities": {"drop": ["ALL"]}}}]}}},
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
        self.assertEqual(15, report["summary"]["scored"])

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
        self.assertEqual(100, report["summary"]["postureScorePercent"])
        self.assertLess(report["summary"]["evidenceCoveragePercent"], 100)
        self.assertTrue(report["summary"]["domains"])

    def test_risky_workload_and_wildcard_rbac_warn(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); raw, base, collection = self.fixture(root)
            raw["roles"]["items"] = [{"kind": "Role", "metadata": {"namespace": "payments", "name": "wide"}, "rules": [{"verbs": ["*"], "resources": ["pods"]}]}]
            pod_spec = base["workloads"]["items"][0]["spec"]["template"]["spec"]
            pod_spec["containers"][0]["securityContext"]["privileged"] = True
            pod_spec["containers"][0]["securityContext"]["allowPrivilegeEscalation"] = True
            pod_spec["containers"][0]["securityContext"]["readOnlyRootFilesystem"] = False
            report = assess(raw, base, collection, root)
        statuses = {item["controlId"]: item["status"] for item in report["controls"]}
        self.assertEqual("WARN", statuses["cis.k8s.rbac.wildcards"])
        self.assertEqual("WARN", statuses["cis.k8s.pod.privileged"])
        self.assertEqual("WARN", statuses["cis.k8s.pod.privilege-escalation"])
        self.assertEqual("WARN", statuses["cis.k8s.pod.read-only-root-filesystem"])

    def test_image_rbac_and_external_service_controls(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); raw, base, collection = self.fixture(root)
            collection["resources"].update({key: {"state": "AVAILABLE"} for key in ("services", "validatingwebhooks", "mutatingwebhooks", "kyverno_clusterpolicies")})
            raw.update({"services": {"items": [{"metadata": {"namespace": "payments", "name": "api"}, "spec": {"type": "LoadBalancer"}}]}, "validatingwebhooks": {"items": []}, "mutatingwebhooks": {"items": []}, "kyverno_clusterpolicies": {"items": []}})
            raw["roles"]["items"] = [{"kind": "Role", "metadata": {"namespace": "payments", "name": "secrets"}, "rules": [{"verbs": ["get"], "resources": ["secrets"]}]}]
            base["workloads"]["items"][0]["spec"]["template"]["spec"]["containers"][0]["image"] = "example/api:latest"
            report = assess(raw, base, collection, root)
        statuses = {item["controlId"]: item["status"] for item in report["controls"]}
        self.assertEqual("WARN", statuses["cis.k8s.image.latest-tag"])
        self.assertEqual("WARN", statuses["cis.k8s.image.digest"])
        self.assertEqual("WARN", statuses["cis.k8s.rbac.secrets"])
        self.assertEqual("WARN", statuses["cis.k8s.network.external-services"])
        self.assertEqual("WARN", statuses["cis.k8s.admission.policy-enforcement"])

    def test_comparison_separates_regression_resolution_and_evidence_loss(self) -> None:
        base = {"summary": {"postureScorePercent": 50, "evidenceCoveragePercent": 100}, "controls": [
            {"controlId": "pass-to-warn", "status": "PASS", "applicability": "APPLICABLE", "managedResponsibility": "CUSTOMER"},
            {"controlId": "warn-to-pass", "status": "WARN", "applicability": "APPLICABLE", "managedResponsibility": "CUSTOMER"},
            {"controlId": "lost", "status": "PASS", "applicability": "APPLICABLE", "managedResponsibility": "CUSTOMER"},
        ]}
        current = {"summary": {"postureScorePercent": 60, "evidenceCoveragePercent": 67}, "controls": [
            {"controlId": "pass-to-warn", "status": "WARN", "applicability": "APPLICABLE", "managedResponsibility": "CUSTOMER"},
            {"controlId": "warn-to-pass", "status": "PASS", "applicability": "APPLICABLE", "managedResponsibility": "CUSTOMER"},
            {"controlId": "lost", "status": "UNKNOWN", "applicability": "EVIDENCE_UNAVAILABLE", "managedResponsibility": "CUSTOMER"},
        ]}
        result = compare_reports(base, current)
        self.assertEqual(1, result["counts"]["REGRESSION"])
        self.assertEqual(1, result["counts"]["RESOLVED"])
        self.assertEqual(1, result["counts"]["EVIDENCE_LOSS"])
        self.assertEqual(10, result["postureDelta"])
        self.assertEqual(-33, result["coverageDelta"])


if __name__ == "__main__":
    unittest.main()
