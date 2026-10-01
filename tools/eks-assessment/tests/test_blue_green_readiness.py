#!/usr/bin/env python3
"""Provider-neutral blue-green readiness and migration gate tests."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import assessment_contracts as contracts
import blue_green_readiness as readiness
import configuration_metadata
import migration_compare


class BlueGreenReadinessTests(unittest.TestCase):
    @staticmethod
    def write(path: Path, value: object) -> None:
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    def collection(self, root: Path, namespace: str = "apps", cluster: str = "cluster-a") -> Path:
        root.mkdir(parents=True, exist_ok=True)
        workload = {
            "apiVersion": "apps/v1", "kind": "Deployment",
            "metadata": {"namespace": namespace, "name": "api"},
            "spec": {"replicas": 2, "template": {"spec": {
                "serviceAccountName": "api",
                "containers": [{
                    "name": "api", "image": "example/api:1.0.0",
                    "env": [{"name": "DB_PASSWORD", "valueFrom": {"secretKeyRef": {"name": "db", "key": "password"}}}],
                    "envFrom": [{"configMapRef": {"name": "settings"}}],
                }],
            }}},
        }
        service = {"apiVersion": "v1", "kind": "Service", "metadata": {"namespace": namespace, "name": "api"}, "spec": {"selector": {"app": "api"}, "ports": [{"name": "http", "port": 8080}]}}
        endpoint = {"apiVersion": "discovery.k8s.io/v1", "kind": "EndpointSlice", "metadata": {"namespace": namespace, "name": "api-1", "labels": {"kubernetes.io/service-name": "api"}}, "endpoints": [{"addresses": ["10.0.0.10"], "conditions": {"ready": True}}]}
        ingress = {"apiVersion": "networking.k8s.io/v1", "kind": "Ingress", "metadata": {"namespace": namespace, "name": "api"}, "spec": {"tls": [{"hosts": ["api.example.test"], "secretName": "api-tls"}], "rules": [{"host": "api.example.test", "http": {"paths": [{"path": "/", "backend": {"service": {"name": "api", "port": {"name": "http"}}}}]}}]}}
        metadata = {
            "schemaVersion": "1.0", "generatedAt": "2026-09-29T00:00:00+00:00", "readOnly": True,
            "state": "AVAILABLE", "namespaceScope": namespace,
            "policy": {"optIn": True, "valuesPersisted": False, "annotationsPersisted": False, "fieldsPersisted": ["kind", "namespace", "name", "type", "immutable", "keys"], "rbacWarning": "temporary identity"},
            "resources": {"configmaps": {"state": "AVAILABLE", "reason": "", "count": 1}, "secrets": {"state": "AVAILABLE", "reason": "", "count": 2}},
            "items": [
                {"kind": "ConfigMap", "namespace": namespace, "name": "settings", "immutable": True, "keys": ["MODE"]},
                {"kind": "Secret", "namespace": namespace, "name": "db", "immutable": False, "keys": ["password"], "type": "Opaque"},
                {"kind": "Secret", "namespace": namespace, "name": "api-tls", "immutable": False, "keys": ["tls.crt", "tls.key"], "type": "kubernetes.io/tls"},
            ],
        }
        documents = {
            "metadata.json": {"status": "COMPLETED", "completed": True, "clusterName": cluster, "context": f"admin@{cluster}", "namespaceScope": namespace},
            "application-manifests-sanitized.json": {"items": [workload]},
            "services.json": {"items": [service]}, "endpointslices.json": {"items": [endpoint]}, "ingresses.json": {"items": [ingress]},
            "pvcs.json": {"items": []}, "jobs.json": {"items": []}, "cronjobs.json": {"items": []},
            "configuration-metadata.json": metadata,
            "serviceaccounts.json": {"items": [{"apiVersion": "v1", "kind": "ServiceAccount", "metadata": {"namespace": namespace, "name": "api"}}]},
            "operational-insights.json": {
                "nodeHealth": {"state": "PASS", "summary": {"nodes": 1, "passed": 1}},
                "manifestSchema": {"state": "PASS"},
                "versions": {"summary": {"endOfSupport": 0, "nodeVersionSkew": False, "lifecycleUnknown": 0}},
            },
            "migration-evidence.json": {"schemaVersion": "1.0", "generatedAt": "2026-09-29T00:00:00Z", "readOnly": True, "dnsValidated": True, "loadBalancerValidated": True, "rollbackAvailable": True},
        }
        for name, value in documents.items():
            self.write(root / name, value)
        return root

    def test_resolved_configuration_traffic_and_node_evidence_produce_go(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            collection = self.collection(Path(temporary) / "eks-green")
            report = readiness.generate(collection)
            self.assertEqual("GO", report["status"])
            self.assertEqual("PASS", json.loads((collection / "configuration-references.json").read_text())["state"])
            self.assertEqual("PASS", json.loads((collection / "traffic-paths.json").read_text())["state"])
            for filename in ("configuration-metadata.json", "configuration-references.json", "traffic-paths.json", "state-data-readiness.json", "migration-probes.json", "blue-green-readiness.json"):
                result = contracts.validate_document(collection / filename, ROOT / "data" / "schemas" / f"{filename[:-5]}.schema.json")
                self.assertEqual("PASS", result["state"], result)

    def test_missing_required_secret_blocks_readiness_without_exposing_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            collection = self.collection(Path(temporary) / "eks-green")
            path = collection / "configuration-metadata.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            value["items"] = [item for item in value["items"] if item["name"] != "db"]
            self.write(path, value)
            report = readiness.generate(collection)
            references = json.loads((collection / "configuration-references.json").read_text(encoding="utf-8"))
            self.assertEqual("NO_GO", report["status"])
            self.assertEqual("FAIL", references["state"])
            self.assertNotIn("super-secret", json.dumps(references))

    def test_unversioned_manual_evidence_never_produces_go(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            collection = self.collection(Path(temporary) / "eks-green")
            self.write(collection / "migration-evidence.json", {"dnsValidated": True, "loadBalancerValidated": True, "rollbackAvailable": True})
            report = readiness.generate(collection)
            gate = next(item for item in report["gates"] if item["gateId"] == "manual-evidence.contract")
            self.assertEqual("NO_GO", report["status"])
            self.assertEqual("FAIL", gate["status"])

    def test_istio_invalid_weight_and_exact_reference_grant_are_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            collection = self.collection(Path(temporary) / "eks-green")
            virtual_service = {"apiVersion": "networking.istio.io/v1", "kind": "VirtualService", "metadata": {"namespace": "apps", "name": "api"}, "spec": {"hosts": ["api"], "http": [{"route": [{"destination": {"host": "api"}, "weight": 70}, {"destination": {"host": "api"}, "weight": 20}]}]}}
            self.write(collection / "istio-virtualservices.json", {"items": [virtual_service]})
            report = readiness.generate(collection)
            self.assertEqual("NO_GO", report["status"])
            traffic = json.loads((collection / "traffic-paths.json").read_text(encoding="utf-8"))
            self.assertTrue(any(item["type"] == "VirtualService" and item["state"] == "FAIL" for item in traffic["paths"]))

            bad_grant = {"apiVersion": "gateway.networking.k8s.io/v1beta1", "kind": "ReferenceGrant", "metadata": {"namespace": "shared", "name": "grant"}, "spec": {"from": [{"group": "gateway.networking.k8s.io", "kind": "HTTPRoute", "namespace": "other"}], "to": [{"group": "", "kind": "Service", "name": "backend"}]}}
            self.assertFalse(readiness.reference_granted([bad_grant], "apps", "HTTPRoute", "shared", "backend"))
            bad_grant["spec"]["from"][0]["namespace"] = "apps"
            self.assertTrue(readiness.reference_granted([bad_grant], "apps", "HTTPRoute", "shared", "backend"))

    def test_cross_namespace_gateway_outside_collection_scope_is_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            collection = self.collection(Path(temporary) / "eks-green")
            virtual_service = {
                "apiVersion": "networking.istio.io/v1", "kind": "VirtualService",
                "metadata": {"namespace": "apps", "name": "api"},
                "spec": {"gateways": ["ingress-system/public"], "hosts": ["api.example.test"], "http": [{"route": [{"destination": {"host": "api", "port": {"number": 8080}}}]}]},
            }
            self.write(collection / "ingresses.json", {"items": []})
            self.write(collection / "istio-virtualservices.json", {"items": [virtual_service]})
            report = readiness.generate(collection)
            traffic = json.loads((collection / "traffic-paths.json").read_text(encoding="utf-8"))
            self.assertEqual("UNKNOWN", report["status"])
            self.assertEqual("UNKNOWN", traffic["paths"][0]["state"])
            self.assertIn("fora do escopo", traffic["paths"][0]["checks"][0]["detail"])

            metadata = json.loads((collection / "metadata.json").read_text(encoding="utf-8"))
            metadata["namespaceScope"] = "*"
            self.write(collection / "metadata.json", metadata)
            report = readiness.generate(collection)
            traffic = json.loads((collection / "traffic-paths.json").read_text(encoding="utf-8"))
            self.assertEqual("NO_GO", report["status"])
            self.assertEqual("FAIL", traffic["paths"][0]["state"])

    def test_database_client_image_is_not_classified_as_stateful_server(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            collection = self.collection(Path(temporary) / "eks-green")
            manifests = json.loads((collection / "application-manifests-sanitized.json").read_text(encoding="utf-8"))
            manifests["items"][0] = {
                "apiVersion": "apps/v1", "kind": "Deployment",
                "metadata": {"namespace": "apps", "name": "database-e2e-client", "labels": {"component": "client"}},
                "spec": {"template": {"spec": {"containers": [{"name": "psql-client", "image": "postgres:17", "command": ["sh", "-ec"], "args": ["exec sleep infinity"]}]}}},
            }
            self.write(collection / "application-manifests-sanitized.json", manifests)
            readiness.generate(collection)
            state_data = json.loads((collection / "state-data-readiness.json").read_text(encoding="utf-8"))
            self.assertEqual([], state_data["statefulWorkloads"])
            self.assertEqual(0, state_data["summary"]["statefulWorkloads"])

    def test_probe_rejects_credentials_metadata_and_query(self) -> None:
        for url in ("http://user:pass@example.test/health", "http://169.254.169.254/latest/meta-data", "https://example.test/health?token=x"):
            with self.assertRaises(ValueError, msg=url):
                readiness.validate_probe_url(url)
        report = readiness.run_probes(["http://user:super-secret@example.test/health"])
        serialized = json.dumps(report)
        self.assertNotIn("super-secret", serialized)
        self.assertNotIn("user@", serialized)
        self.assertEqual("http://example.test/health", report["items"][0]["url"])

    def test_offline_generation_ignores_probe_urls_from_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            collection = self.collection(Path(temporary) / "eks-green")
            with patch.dict(os.environ, {"ASSESSMENT_PROBE_URLS": "http://169.254.169.254/latest/meta-data"}):
                readiness.generate(collection, include_environment_probes=False)
            probes = json.loads((collection / "migration-probes.json").read_text(encoding="utf-8"))
            self.assertEqual("DISABLED", probes["state"])
            self.assertEqual([], probes["items"])

    def test_stateful_workload_requires_backup_replication_restore_and_rollback_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            collection = self.collection(Path(temporary) / "eks-green")
            manifests = json.loads((collection / "application-manifests-sanitized.json").read_text(encoding="utf-8"))
            manifests["items"].append({
                "apiVersion": "apps/v1", "kind": "StatefulSet", "metadata": {"namespace": "apps", "name": "postgres"},
                "spec": {"template": {"spec": {"containers": [{"name": "postgres", "image": "postgres:17"}], "volumes": [{"name": "data", "persistentVolumeClaim": {"claimName": "postgres-data"}}]}}},
            })
            self.write(collection / "application-manifests-sanitized.json", manifests)
            self.write(collection / "pvcs.json", {"items": [{"apiVersion": "v1", "kind": "PersistentVolumeClaim", "metadata": {"namespace": "apps", "name": "postgres-data"}, "spec": {"storageClassName": "fast", "accessModes": ["ReadWriteOnce"], "resources": {"requests": {"storage": "20Gi"}}}, "status": {"phase": "Bound"}}]})
            evidence = json.loads((collection / "migration-evidence.json").read_text(encoding="utf-8"))
            evidence.pop("rollbackAvailable")
            self.write(collection / "migration-evidence.json", evidence)
            report = readiness.generate(collection)
            state_data = json.loads((collection / "state-data-readiness.json").read_text(encoding="utf-8"))
            self.assertEqual("UNKNOWN", report["status"])
            self.assertEqual("UNKNOWN", state_data["state"])
            unknown = {item["gateId"] for item in state_data["gates"] if item["status"] == "UNKNOWN"}
            self.assertTrue({"data.backup-health", "data.snapshot-health", "data.replication", "data.restore-tested", "application.schema-compatibility", "rollback.available"}.issubset(unknown))

    def test_migration_mapping_allows_different_cluster_namespace_and_image(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self.collection(root / "eks-blue", "blue", "cluster-a")
            target = self.collection(root / "eks-green", "green", "cluster-b")
            readiness.generate(source)
            readiness.generate(target)
            probe = {"schemaVersion": "1.0", "generatedAt": readiness.utc_iso(), "readOnly": True, "state": "PASS", "summary": {"probes": 1, "passed": 1, "warnings": 0, "failed": 0, "maxLatencyMs": 2000}, "items": [{"url": "https://green.example.test/health", "state": "PASS", "httpStatus": 200, "latencyMs": 20, "tlsValidated": True}], "policy": {"method": "GET", "redirects": "DENIED", "responseBodyPersisted": False, "credentialsAllowed": False}}
            self.write(target / "migration-probes.json", probe)
            manifests = json.loads((target / "application-manifests-sanitized.json").read_text(encoding="utf-8"))
            manifests["items"][0]["spec"]["template"]["spec"]["containers"][0]["image"] = "example/api:2.0.0"
            self.write(target / "application-manifests-sanitized.json", manifests)
            mapping = migration_compare.normalize_mapping({"namespaceMap": {"blue": "green"}, "allowedDifferences": ["image", "replicas"]})
            report = migration_compare.evaluate(source, target, mapping)
            self.assertEqual("GO", report["status"], report["gates"])
            self.assertNotEqual(report["source"]["clusterName"], report["target"]["clusterName"])
            self.assertNotIn("context", report["source"])
            self.assertNotIn("context", report["target"])
            difference = next(item for item in next(gate for gate in report["gates"] if gate["gateId"] == "migration.workload-parity")["evidence"]["differences"] if item["field"] == "images")
            self.assertTrue(difference["allowed"])
            self.assertEqual(["image"], migration_compare.normalize_mapping({"allowedDifferences": ["image", "image"]})["allowedDifferences"])

    def test_migration_missing_service_blocks_cutover(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self.collection(root / "eks-blue", "blue", "cluster-a")
            target = self.collection(root / "eks-green", "green", "cluster-b")
            readiness.generate(source); readiness.generate(target)
            self.write(target / "services.json", {"items": []})
            probe = json.loads((target / "migration-probes.json").read_text(encoding="utf-8"))
            probe.update(state="PASS", summary={"probes": 1, "passed": 1, "warnings": 0, "failed": 0, "maxLatencyMs": 2000}, items=[{"url": "https://green.example.test/health", "state": "PASS", "httpStatus": 200, "latencyMs": 20, "tlsValidated": True}])
            self.write(target / "migration-probes.json", probe)
            report = migration_compare.evaluate(source, target, migration_compare.normalize_mapping({"namespaceMap": {"blue": "green"}}))
            gate = next(item for item in report["gates"] if item["gateId"] == "migration.service-parity")
            self.assertEqual("NO_GO", report["status"])
            self.assertEqual("FAIL", gate["status"])
            self.assertEqual(1, len(gate["evidence"]["missing"]))

    def test_configuration_metadata_collector_keeps_only_allowlisted_fields(self) -> None:
        payload = {"items": [{"metadata": {"namespace": "apps", "name": "db", "annotations": {"owner": "secret"}}, "type": "Opaque", "data": {"password": "super-secret"}}]}
        completed = type("Completed", (), {"returncode": 0, "stdout": json.dumps(payload), "stderr": ""})()
        with patch.object(configuration_metadata.subprocess, "run", return_value=completed):
            report = configuration_metadata.collect("apps", False, True, 30)
        serialized = json.dumps(report)
        self.assertNotIn("super-secret", serialized)
        self.assertNotIn("owner", serialized)
        self.assertEqual({"kind", "namespace", "name", "immutable", "keys", "type"}, set(report["items"][0]))
        self.assertEqual(["password"], report["items"][0]["keys"])


if __name__ == "__main__":
    unittest.main()
