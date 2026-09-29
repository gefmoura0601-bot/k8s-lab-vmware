"""Regression tests for the portable 0.5 foundation."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import assessment_contracts as contracts
import collection_bundle as bundles
import collector_registry as collectors
import manifest_schema_validation as manifests
import provider_validation as provider
import release_verification as release
from assessment_process_supervisor import CollectionSupervisor


ROOT = Path(__file__).resolve().parents[1]


class Foundation050Tests(unittest.TestCase):
    def test_contract_validation_supports_partial_collection_but_rejects_invalid_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            collection = Path(temporary) / "eks-partial"
            collection.mkdir()
            metadata = {"id": collection.name, "createdAt": "2026-09-29T00:00:00Z", "status": "RUNNING", "completed": False, "readOnly": True}
            (collection / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
            report = contracts.validate_collection(collection)
            self.assertEqual("PASS", report["summary"]["state"])
            metadata["readOnly"] = False
            (collection / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
            report = contracts.validate_collection(collection)
            self.assertEqual("FAIL", report["summary"]["state"])
            self.assertIn("expected constant True", " ".join(report["artifacts"][0]["errors"]))

    def test_collector_registry_enforces_dependencies_and_weighted_resume(self) -> None:
        registry = collectors.load_registry(ROOT / "data/collectors.json")
        plan = collectors.build_plan(registry, channel="cli", prometheus=False, include={"assessment"})
        self.assertIn("preflight", [item["id"] for item in plan])
        self.assertIn("contract-validation", [item["id"] for item in plan])
        with self.assertRaises(ValueError):
            collectors.build_plan(registry, channel="cli", prometheus=False, include={"prometheus"})
        with self.assertRaises(ValueError):
            collectors.build_plan(registry, channel="cli", prometheus=False, exclude={"preflight"})
        with tempfile.TemporaryDirectory() as temporary:
            collection = Path(temporary) / "eks-resume"
            collection.mkdir()
            state = collectors.init_state(collection, plan, resume=False, retry_failed=False)
            self.assertEqual(0, state["progressPercent"])
            state = collectors.update_state(collection, "preflight", "PASS", 0, "")
            self.assertGreater(state["progressPercent"], 0)
            state = collectors.update_state(collection, "assessment", "FAIL", 1, "test")
            resumed = collectors.init_state(collection, plan, resume=True, retry_failed=True)
            self.assertEqual("PENDING", resumed["collectors"]["assessment"]["state"])
            self.assertEqual("PENDING", resumed["collectors"]["preflight"]["state"])

    def test_supervisor_uses_component_weights(self) -> None:
        supervisor = CollectionSupervisor()
        supervisor.start("weighted", 60, ["small", "large"], {"small": 1, "large": 9})
        result = supervisor.run("small", [sys.executable, "-c", "pass"], timeout=5)
        self.assertEqual(0, result.returncode)
        self.assertEqual(10, supervisor.status()["progressPercent"])
        supervisor.finish("FAILED")

    def test_manifest_semantics_detect_selector_and_deprecated_api(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            collection = Path(temporary)
            value = {
                "schemaVersion": "4.0",
                "notice": "sanitized",
                "items": [{
                    "apiVersion": "apps/v1beta1",
                    "kind": "Deployment",
                    "metadata": {"name": "api", "namespace": "apps"},
                    "spec": {"selector": {"matchLabels": {"app": "api"}}, "template": {"metadata": {"labels": {"app": "other"}}, "spec": {"containers": [{"name": "api"}]}}},
                }],
            }
            (collection / "application-manifests-sanitized.json").write_text(json.dumps(value), encoding="utf-8")
            (collection / "api-resources.json").write_text(json.dumps({"state": "AVAILABLE", "namespaced": ["deployments.apps"], "clusterScoped": []}), encoding="utf-8")
            report = manifests.evaluate_collection(collection)
            self.assertEqual("FAIL", report["state"])
            rules = {item["ruleId"] for item in report["findings"]}
            self.assertIn("manifest.deprecated-api", rules)
            self.assertIn("manifest.selector-labels", rules)
            self.assertIn("manifest.container-image", rules)

    def test_bundle_round_trip_checks_integrity_and_excludes_logs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            collection = root / "eks-portable"
            collection.mkdir()
            (collection / "metadata.json").write_text('{"readOnly":true}', encoding="utf-8")
            (collection / "evidence.json").write_text('{"state":"PASS"}', encoding="utf-8")
            (collection / "collector.log").write_text("not portable", encoding="utf-8")
            archive = root / "collection.tar.gz"
            with patch.object(bundles, "validate_collection", return_value={"summary": {"state": "PASS"}}), patch.object(
                bundles, "run_artifact_validation", return_value={"ok": True, "errors": [], "inventory": {}}
            ):
                exported = bundles.export_bundle(collection, archive, "0.5.0-rc.1")
            self.assertTrue(exported["ok"])
            verified = bundles.verify_bundle(archive)
            self.assertEqual(2, verified["files"])
            imported_root = root / "imported"
            imported = bundles.import_bundle(archive, imported_root)
            self.assertTrue(Path(imported["importedTo"], "evidence.json").is_file())
            self.assertFalse(Path(imported["importedTo"], "collector.log").exists())

    def test_release_gate_writes_json_junit_sarif_and_markdown(self) -> None:
        report = {
            "summary": {"state": "FAIL", "releaseReady": False, "gates": 1},
            "provider": {"expected": "generic-kubernetes"},
            "collection": {"reference": "eks-test"},
            "gates": [{"gateId": "test.gate", "category": "Test", "status": "FAIL", "mandatory": True, "summary": "Bloqueado", "evidence": {"reason": "test"}}],
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = [root / "report.json", root / "report.xml", root / "report.sarif.json", root / "report.md"]
            provider.write_outputs(report, *paths)
            self.assertEqual("FAIL", json.loads(paths[0].read_text(encoding="utf-8"))["summary"]["state"])
            self.assertIn("<failure", paths[1].read_text(encoding="utf-8"))
            self.assertEqual("2.1.0", json.loads(paths[2].read_text(encoding="utf-8"))["version"])
            self.assertIn("Release Ready", paths[3].read_text(encoding="utf-8"))

    def test_release_verification_checks_archive_sbom_and_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "eks-assessment-0.5.0-rc.1"
            package.mkdir()
            (package / "VERSION").write_text("0.5.0-rc.1\n", encoding="utf-8")
            (package / "README.md").write_text("portable\n", encoding="utf-8")
            inventory = []
            for name in ("README.md", "VERSION"):
                digest = hashlib.sha256((package / name).read_bytes()).hexdigest()
                inventory.append(f"FileName: ./{name}\nFileChecksum: SHA256: {digest}\n")
            sbom = package / "SBOM.spdx"
            sbom.write_text("\n".join(inventory), encoding="utf-8")
            external_sbom = root / "eks-assessment-0.5.0-rc.1.spdx"
            external_sbom.write_bytes(sbom.read_bytes())
            archive = root / "eks-assessment-0.5.0-rc.1.tar.gz"
            with tarfile.open(archive, "w:gz") as handle:
                handle.add(package, arcname=package.name)
            archive_digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            checksum = root / f"{archive.name}.sha256"
            checksum.write_text(f"{archive_digest}  {archive.name}\n", encoding="utf-8")
            provenance = root / "provenance.json"
            provenance.write_text(json.dumps({
                "subject": {"name": archive.name, "sha256": archive_digest},
                "materials": {"sbomSha256": hashlib.sha256(external_sbom.read_bytes()).hexdigest()},
                "builder": "test", "sourceCommit": "0" * 40,
            }), encoding="utf-8")
            args = argparse.Namespace(
                archive=archive, checksum=checksum, sbom=external_sbom, provenance=provenance,
                sigstore_bundle=None, certificate_identity="", oidc_issuer="", require_signature=False,
            )
            report = release.evaluate(args)
            self.assertTrue(report["ok"])
            self.assertEqual("VERIFIED", report["provenance"]["state"])


if __name__ == "__main__":
    unittest.main()
