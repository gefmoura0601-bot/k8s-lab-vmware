#!/usr/bin/env python3
"""Regression matrix for the provider-neutral offline Regression Gate."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import regression_validation as validation


class RegressionValidationTests(unittest.TestCase):
    @staticmethod
    def write_json(path: Path, value: object) -> None:
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def finding(ident: str, severity: str, *, category: str = "Security") -> dict:
        return {
            "id": ident,
            "fingerprint": ident,
            "ruleId": f"k8s.{ident}",
            "resourceKey": f"apps/Deployment/{ident}",
            "evidenceHash": f"hash-{ident}-{severity}",
            "severity": severity,
            "status": "ATUAL",
            "category": category,
            "check": ident,
            "namespace": "apps",
            "workload": f"Deployment/{ident}",
            "container": "api",
            "detail": "evidência sanitizada",
            "recommendation": "Revisar configuração.",
            "confidence": "HIGH",
            "applicability": "APPLICABLE",
        }

    def collection(self, root: Path, *, cluster: str = "sensitive-lab", findings: list[dict] | None = None) -> Path:
        root.mkdir(parents=True, exist_ok=True)
        findings = findings or []
        counts = {state: sum(item["severity"] == state for item in findings) for state in ("CRIT", "WARN", "UNKNOWN", "PARTIAL", "PASS")}
        metadata = {
            "status": "COMPLETED", "completed": True, "clusterName": cluster,
            "context": f"operator@{cluster}", "performance": {"durationSeconds": 100.0},
        }
        nodes = {"items": [{
            "metadata": {"name": "node-1"},
            "status": {"conditions": [{"type": "Ready", "status": "True"}], "nodeInfo": {"kubeletVersion": "v1.35.0"}},
        }]}
        pods = {"items": [{
            "metadata": {"namespace": "apps", "name": "api-1"},
            "spec": {"nodeName": "node-1", "containers": [{"name": "api", "image": "example/api:1.0.0"}]},
            "status": {"phase": "Running"},
        }]}
        workloads = {"items": [{
            "apiVersion": "apps/v1", "kind": "Deployment",
            "metadata": {"namespace": "apps", "name": "api"},
            "spec": {"template": {"spec": {"containers": [{"name": "api", "image": "example/api:1.0.0"}]}}},
        }]}
        quality = {"state": "PASS", "stableIdentityDuplicates": 0, "conflictingSeverities": 0, "lowConfidencePasses": 0}
        comprehensive = {
            "schemaVersion": "4.0", "readOnly": True,
            "safety": {"kubectlVerbs": ["get", "list"], "mutations": 0},
            "summary": {
                "workloads": 1, "containers": 1, "checks": max(1, len(findings)),
                "critical": counts["CRIT"], "warnings": counts["WARN"],
                "unknown": counts["UNKNOWN"], "partial": counts["PARTIAL"], "passed": counts["PASS"],
            },
            "findings": findings,
            "quality": quality,
            "collection": {"resources": {"nodes": {"state": "AVAILABLE"}, "pods": {"state": "AVAILABLE"}}},
            "performance": {
                "requestBudget": {"requests": 100, "retries": 0, "throttles": 0, "responseBytes": 1024, "elapsedSeconds": 90},
                "processPeakRssBytes": 1024 * 1024,
            },
        }
        cis = {
            "schemaVersion": "1.1", "readOnly": True,
            "notice": "Avaliação baseada no CIS. Não representa certificação nem compliance integral.",
            "controls": [{
                "controlId": "cis.k8s.pod.nonroot", "title": "Execução não root", "domain": "Pod Security",
                "status": "PASS", "applicability": "APPLICABLE", "managedResponsibility": "CUSTOMER",
                "evidenceSource": "KubernetesAPI", "assessmentMode": "AUTOMATED", "riskWeight": 3,
                "validationCommand": "kubectl get pods -A", "evidence": {}, "recommendation": "Manter configuração.",
            }],
            "summary": {"postureScorePercent": 100, "evidenceCoveragePercent": 100, "domains": []},
        }
        operational = {
            "schemaVersion": "1.2", "readOnly": True, "platform": "generic-kubernetes",
            "diagnostics": {"summary": {"warnings": 0}},
            "nodeHealth": {
                "state": "PASS",
                "summary": {"nodes": 1, "critical": 0, "warnings": 0, "partial": 0, "passed": 1, "metricsNodes": 1, "metricsCoveragePercent": 100.0},
                "items": [{"node": "node-1", "state": "PASS", "ready": True, "evidence": {"metrics": "MetricsAPI"}}],
            },
            "versions": {"summary": {"components": 1, "nodeVersionSkew": False, "endOfSupport": 0, "lifecycleUnknown": 0}},
            "manifestQuality": {"summary": {"resources": 1, "issues": 0, "critical": 0, "warnings": 0}, "findings": []},
            "containerTuning": {"summary": {}, "recommendations": []},
            "bestPractices": {"platform": "generic-kubernetes", "rules": []},
            "logs": {"state": "DISABLED", "entries": []},
        }
        aws = {"schemaVersion": "1.0", "readOnly": True, "state": "N/A", "safety": {"mutations": 0}, "coverage": {}, "inventory": {}, "findings": []}
        cloud = {
            "schemaVersion": "1.0", "readOnly": True, "provider": "generic-kubernetes", "state": "N/A",
            "safety": {"operations": ["get", "list"], "mutations": 0, "credentialsPersisted": False,
                       "accountIdentifiers": "omitted", "rawPayloadsPersisted": False, "requests": 0},
            "coverage": {}, "bestPractices": [], "summary": {},
        }
        documents = {
            "metadata.json": metadata,
            "nodes.json": nodes,
            "pods.json": pods,
            "workloads.json": workloads,
            "comprehensive-assessment.json": comprehensive,
            "application-manifests-sanitized.json": {"items": []},
            "api-resources.json": {"state": "AVAILABLE", "resources": []},
            "universal-inventory.json": {"schemaVersion": "4.0", "resourceTypes": 1, "objectCount": 3, "resources": []},
            "aws-eks-assessment.json": aws,
            "cloud-provider-assessment.json": cloud,
            "cis-security-assessment.json": cis,
            "operational-insights.json": operational,
        }
        for name, value in documents.items():
            self.write_json(root / name, value)
        return root

    def update(self, collection: Path, filename: str, callback) -> None:
        path = collection / filename
        value = json.loads(path.read_text(encoding="utf-8"))
        callback(value)
        self.write_json(path, value)

    def test_standard_profile_passes_without_regression_and_outputs_are_valid(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            before = self.collection(root / "eks-before")
            after = self.collection(root / "eks-after")
            report = validation.evaluate(before, after)
            self.assertEqual("PASS", report["summary"]["state"])
            self.assertTrue(report["summary"]["releaseReady"])
            self.assertEqual("standard", report["policy"]["profile"])
            self.assertEqual(64, len(report["policy"]["sha256"]))
            self.assertEqual(64, len(report["comparison"]["beforeSha256"]))
            self.assertEqual(64, len(report["comparison"]["afterSha256"]))
            self.assertNotIn("sensitive-lab", json.dumps(report))
            ET.fromstring(validation.junit_xml(report))
            sarif = validation.sarif(report)
            self.assertEqual("2.1.0", sarif["version"])
            self.assertEqual([], sarif["runs"][0]["results"])

    def test_new_risk_severity_regression_and_evidence_loss_block_release(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            before = self.collection(root / "eks-before", findings=[
                self.finding("worse", "PASS"), self.finding("lost", "PASS"),
            ])
            after = self.collection(root / "eks-after", findings=[
                self.finding("worse", "WARN"), self.finding("lost", "UNKNOWN"), self.finding("new-critical", "CRIT"),
            ])
            report = validation.evaluate(before, after)
            states = {item["gateId"]: item["status"] for item in report["gates"]}
            self.assertEqual("FAIL", states["findings.new-risk"])
            self.assertEqual("FAIL", states["findings.severity-regression"])
            self.assertEqual("FAIL", states["evidence.loss"])
            self.assertFalse(report["summary"]["releaseReady"])
            self.assertEqual({"NEW_RISK", "SEVERITY_REGRESSION", "EVIDENCE_LOSS"}, {item["change"] for item in report["changes"]})

    def test_cis_node_manifest_lifecycle_quality_and_performance_regressions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            before = self.collection(root / "eks-before")
            after = self.collection(root / "eks-after")
            self.update(after, "cis-security-assessment.json", lambda value: (
                value["controls"][0].update(status="WARN"),
                value["summary"].update(postureScorePercent=70, evidenceCoveragePercent=80),
            ))
            self.update(after, "operational-insights.json", lambda value: (
                value["nodeHealth"]["summary"].update(critical=1, warnings=1, metricsCoveragePercent=50),
                value["nodeHealth"]["items"][0].update(state="CRIT", ready=False),
                value["manifestQuality"]["summary"].update(critical=1, warnings=1),
                value["versions"]["summary"].update(endOfSupport=1, nodeVersionSkew=True),
            ))
            self.update(after, "comprehensive-assessment.json", lambda value: (
                value["quality"].update(stableIdentityDuplicates=1),
                value["performance"]["requestBudget"].update(requests=200, responseBytes=4096),
                value["performance"].update(processPeakRssBytes=4 * 1024 * 1024),
            ))
            self.update(after, "metadata.json", lambda value: value["performance"].update(durationSeconds=200))
            report = validation.evaluate(before, after, validate_artifacts=False)
            states = {item["gateId"]: item["status"] for item in report["gates"]}
            for gate_id in ("cis.regression", "node-health.regression", "operational.regression", "quality.regression", "performance.regression"):
                self.assertEqual("FAIL", states[gate_id], gate_id)

    def test_scope_terminal_and_missing_operational_evidence_never_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            before = self.collection(root / "eks-before", cluster="cluster-a")
            after = self.collection(root / "eks-after", cluster="cluster-b")
            self.update(after, "metadata.json", lambda value: value.update(status="CANCELLED", completed=False))
            self.update(after, "operational-insights.json", lambda value: value.pop("nodeHealth"))
            report = validation.evaluate(before, after, validate_artifacts=False)
            states = {item["gateId"]: item["status"] for item in report["gates"]}
            self.assertEqual("FAIL", states["comparison.scope"])
            self.assertEqual("FAIL", states["collections.terminal-state"])
            self.assertEqual("FAIL", states["node-health.regression"])

    def test_strict_profile_blocks_legacy_risk_that_standard_allows(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy = [self.finding("legacy", "WARN")]
            before = self.collection(root / "eks-before", findings=legacy)
            after = self.collection(root / "eks-after", findings=legacy)
            standard = validation.evaluate(before, after)
            strict = validation.evaluate(before, after, profile="strict")
            self.assertTrue(standard["summary"]["releaseReady"])
            self.assertFalse(strict["summary"]["releaseReady"])
            gate = next(item for item in strict["gates"] if item["gateId"] == "findings.current-risk")
            self.assertEqual("FAIL", gate["status"])

    def test_cli_writes_json_junit_and_sarif_with_release_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            before = self.collection(root / "eks-before")
            after = self.collection(root / "eks-after")
            result = subprocess.run(
                [sys.executable, str(ROOT / "src" / "regression_validation.py"), "--before", str(before), "--after", str(after)],
                text=True, capture_output=True, check=False, timeout=30,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertTrue(json.loads(result.stdout)["releaseReady"])
            report = json.loads((after / "regression-validation.json").read_text(encoding="utf-8"))
            self.assertTrue(report["summary"]["releaseReady"])
            ET.parse(after / "regression-validation.junit.xml")
            sarif = json.loads((after / "regression-validation.sarif.json").read_text(encoding="utf-8"))
            self.assertEqual("2.1.0", sarif["version"])
            self.assertFalse((after / ".regression-validation.json.tmp").exists())

    def test_invalid_policy_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            policy = Path(temporary) / "assessment-policy.json"
            self.write_json(policy, {"schemaVersion": "1.0", "defaultProfile": "broken", "profiles": {"broken": {}}})
            with self.assertRaisesRegex(ValueError, "campos obrigatórios"):
                validation.load_policy(policy)

            document = json.loads(validation.DEFAULT_POLICY.read_text(encoding="utf-8"))
            document["profiles"]["standard"]["requireSameCluster"] = False
            self.write_json(policy, document)
            with self.assertRaisesRegex(ValueError, "não pode ser desabilitado"):
                validation.load_policy(policy, "standard")


if __name__ == "__main__":
    unittest.main()
