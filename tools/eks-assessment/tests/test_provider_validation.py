#!/usr/bin/env python3
"""Regression matrix for the offline Provider Validation Runner."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import provider_validation as validation


class ProviderValidationTests(unittest.TestCase):
    @staticmethod
    def write_json(path: Path, value: object) -> None:
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    def collection(self, root: Path, provider: str, cloud_state: str | None = None) -> Path:
        root.mkdir(parents=True, exist_ok=True)
        managed = provider in {"eks", "aks", "gke"}
        state = cloud_state or ("AVAILABLE" if managed else "N/A")
        provider_ids = {
            "eks": "aws:///us-east-1a/i-sanitized",
            "aks": "azure:///sanitized",
            "gke": "gce://sanitized",
            "generic-kubernetes": "",
        }
        contexts = {
            "eks": "arn:aws:eks:us-east-1:000000000000:cluster/sanitized",
            "aks": "aks-sanitized",
            "gke": "gke_sanitized_region_cluster",
            "generic-kubernetes": "kubernetes-admin@sanitized",
        }
        metadata = {
            "status": "COMPLETED",
            "completed": True,
            "clusterName": "sensitive-cluster-name",
            "context": contexts[provider],
            "performance": {"durationSeconds": 120.0},
        }
        nodes = {"items": [{
            "metadata": {"name": "node-sanitized", "labels": {}},
            "spec": {"providerID": provider_ids[provider]},
            "status": {"nodeInfo": {"kubeletVersion": "v1.35.0"}},
        }]}
        pods = {"items": [{
            "metadata": {"namespace": "apps", "name": "api-sanitized"},
            "spec": {"nodeName": "node-sanitized", "containers": [{"name": "api", "image": "example/api:1.0.0"}]},
            "status": {"phase": "Running"},
        }]}
        workloads = {"items": [{
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"namespace": "apps", "name": "api-sanitized"},
            "spec": {"template": {"spec": {"containers": [{"name": "api", "image": "example/api:1.0.0"}]}}},
        }]}
        aws = {
            "schemaVersion": "1.0",
            "readOnly": True,
            "state": "AVAILABLE" if provider == "eks" else "N/A",
            "safety": {"mutations": 0, "requests": 4 if provider == "eks" else 0},
            "coverage": {},
            "inventory": {},
            "findings": [],
        }
        cloud_rules = [] if not managed else [{
            "ruleId": f"bestpractice.{provider}.sample",
            "domain": "Security",
            "status": "PASS",
            "applicability": "APPLICABLE",
            "responsibility": "SHARED",
            "resource": "cluster",
            "evidence": "sanitized=true",
            "recommendation": "Manter a configuração validada.",
        }]
        cloud = {
            "schemaVersion": "1.0",
            "readOnly": True,
            "provider": provider,
            "state": state,
            "reason": "",
            "safety": {
                "operations": ["get", "list", "describe", "show"],
                "mutations": 0,
                "credentialsPersisted": False,
                "accountIdentifiers": "omitted",
                "rawPayloadsPersisted": False,
                "requests": 4 if managed else 0,
            },
            "coverage": {"cluster": {"state": "AVAILABLE"}} if managed else {},
            "cluster": {"version": "1.35.0"} if managed else {},
            "lifecycle": {},
            "bestPractices": cloud_rules,
            "summary": {"rules": len(cloud_rules), "coverageAvailable": 1 if managed else 0},
        }
        operational_rules = []
        for candidate in ("eks", "aks", "gke"):
            applicable = candidate == provider
            operational_rules.append({
                "ruleId": f"bestpractice.{candidate}.sample",
                "provider": candidate,
                "domain": "Security",
                "status": "PASS" if applicable else "N/A",
                "applicability": "APPLICABLE" if applicable else "NOT_APPLICABLE",
                "responsibility": "SHARED" if applicable else "CLOUD_PROVIDER",
                "resource": "cluster",
                "evidence": "sanitized",
                "recommendation": "Revisar evidência.",
            })
        operational = {
            "schemaVersion": "1.2",
            "readOnly": True,
            "platform": provider,
            "diagnostics": {},
            "nodeHealth": {"state": "PARTIAL", "items": []},
            "versions": {},
            "manifestQuality": {},
            "containerTuning": {},
            "bestPractices": {"platform": provider, "rules": operational_rules},
            "logs": {"state": "DISABLED", "entries": []},
        }
        controls = []
        for control_id in sorted(validation.CONTROL_PLANE_CONTROLS):
            controls.append({
                "controlId": control_id,
                "profile": "generic-kubernetes",
                "evidenceSource": "CloudProviderAPI" if managed else "ManualEvidence",
                "applicability": "MANAGED_PROVIDER" if managed else "MANUAL_REVIEW",
                "assessmentMode": "MANUAL",
                "status": "N/A" if managed else "UNKNOWN",
                "managedResponsibility": "CLOUD_PROVIDER" if managed else "SHARED",
                "evidence": {},
                "recommendation": "Revisar responsabilidade compartilhada.",
                "domain": "Control Plane",
                "riskWeight": 1,
                "validationCommand": "Revisar evidência read-only do provider.",
            })
        cis = {
            "schemaVersion": "1.1",
            "readOnly": True,
            "notice": "Não representa certificação nem compliance integral.",
            "controls": controls,
            "summary": {"postureScorePercent": 0, "evidenceCoveragePercent": 0, "domains": []},
        }
        comprehensive = {
            "schemaVersion": "4.0",
            "readOnly": True,
            "safety": {"kubectlVerbs": ["get", "list"], "mutations": 0},
            "summary": {"workloads": 1, "containers": 1, "checks": 1, "unknown": 0, "partial": 0},
            "findings": [],
            "quality": {"stableIdentityDuplicates": 0, "conflictingSeverities": 0, "lowConfidencePasses": 0},
            "collection": {"resources": {"nodes": {"state": "AVAILABLE"}, "pods": {"state": "AVAILABLE"}}},
            "performance": {
                "requestBudget": {"requests": 100, "retries": 0, "throttles": 0, "responseBytes": 16 * 1024 * 1024, "elapsedSeconds": 100.0},
                "processPeakRssBytes": 128 * 1024 * 1024,
            },
            "cloudProvider": cloud,
        }
        universal = {"schemaVersion": "4.0", "resourceTypes": 2, "objectCount": 3, "resources": []}
        documents = {
            "metadata.json": metadata,
            "nodes.json": nodes,
            "pods.json": pods,
            "workloads.json": workloads,
            "comprehensive-assessment.json": comprehensive,
            "application-manifests-sanitized.json": {"items": []},
            "api-resources.json": {"state": "AVAILABLE", "resources": []},
            "universal-inventory.json": universal,
            "aws-eks-assessment.json": aws,
            "cloud-provider-assessment.json": cloud,
            "cis-security-assessment.json": cis,
            "operational-insights.json": operational,
        }
        for filename, value in documents.items():
            self.write_json(root / filename, value)
        return root

    def test_provider_matrix_passes_with_complete_sanitized_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            for provider in validation.PROVIDERS:
                with self.subTest(provider=provider):
                    report = validation.evaluate(self.collection(base / provider, provider), provider)
                    self.assertEqual("PASS", report["summary"]["state"])
                    self.assertTrue(report["summary"]["releaseReady"])
                    self.assertTrue(all(item["status"] in {"PASS", "N/A"} for item in report["gates"]))

    def test_provider_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            collection = self.collection(Path(temporary) / "eks", "eks")
            report = validation.evaluate(collection, "aks")
            detection = next(item for item in report["gates"] if item["gateId"] == "provider.detection")
            self.assertEqual("FAIL", detection["status"])
            self.assertFalse(report["summary"]["releaseReady"])

    def test_partial_cloud_evidence_requires_review(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            collection = self.collection(Path(temporary) / "aks", "aks", cloud_state="PARTIAL")
            report = validation.evaluate(collection, "aks")
            cloud_gate = next(item for item in report["gates"] if item["gateId"] == "provider.cloud-api")
            self.assertEqual("WARN", cloud_gate["status"])
            self.assertEqual("WARN", report["summary"]["state"])
            self.assertFalse(report["summary"]["releaseReady"])

    def test_mutation_or_foreign_provider_rule_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            collection = self.collection(Path(temporary) / "gke", "gke")
            cloud = json.loads((collection / "cloud-provider-assessment.json").read_text(encoding="utf-8"))
            cloud["safety"]["mutations"] = 1
            self.write_json(collection / "cloud-provider-assessment.json", cloud)
            operational = json.loads((collection / "operational-insights.json").read_text(encoding="utf-8"))
            foreign = next(item for item in operational["bestPractices"]["rules"] if item["provider"] == "eks")
            foreign.update(status="PASS", applicability="APPLICABLE")
            self.write_json(collection / "operational-insights.json", operational)

            report = validation.evaluate(collection, "gke")

            statuses = {item["gateId"]: item["status"] for item in report["gates"]}
            self.assertEqual("FAIL", statuses["safety.read-only"])
            self.assertEqual("FAIL", statuses["provider.applicability"])
            self.assertEqual("FAIL", report["summary"]["state"])

    def test_performance_budget_is_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            collection = self.collection(Path(temporary) / "generic", "generic-kubernetes")
            thresholds = validation.Thresholds(max_api_requests=10)
            report = validation.evaluate(collection, "generic-kubernetes", thresholds)
            performance = next(item for item in report["gates"] if item["gateId"] == "performance.budget")
            self.assertEqual("FAIL", performance["status"])
            self.assertEqual(["apiRequests"], performance["evidence"]["exceeded"])

    def test_report_does_not_copy_cluster_context_or_account_identifier(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            collection = self.collection(Path(temporary) / "eks", "eks")
            report = validation.evaluate(collection, "eks")
            serialized = json.dumps(report, ensure_ascii=False)
            self.assertNotIn("sensitive-cluster-name", serialized)
            self.assertNotIn("000000000000", serialized)
            self.assertNotIn("arn:aws:eks", serialized)

    def test_cli_writes_report_and_returns_success_only_when_release_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            collection = self.collection(Path(temporary) / "generic", "generic-kubernetes")
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "src" / "provider_validation.py"),
                    "--collection", str(collection),
                    "--expected-provider", "generic-kubernetes",
                ],
                text=True,
                capture_output=True,
                check=False,
                timeout=30,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            summary = json.loads(result.stdout)
            self.assertTrue(summary["releaseReady"])
            report = json.loads((collection / "provider-validation.json").read_text(encoding="utf-8"))
            self.assertEqual("PASS", report["summary"]["state"])


if __name__ == "__main__":
    unittest.main()
