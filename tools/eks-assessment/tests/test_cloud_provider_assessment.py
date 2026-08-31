"""Sanitized read-only cloud provider evidence tests."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import cloud_provider_assessment as cloud


class CloudProviderAssessmentTests(unittest.TestCase):
    @staticmethod
    def snapshot(root: Path, provider_id: str, context: str, cluster: str = "cluster-safe") -> None:
        (root / "nodes.json").write_text(json.dumps({"items": [{"metadata": {"labels": {}}, "spec": {"providerID": provider_id}}]}), encoding="utf-8")
        (root / "metadata.json").write_text(json.dumps({"context": context, "clusterName": cluster}), encoding="utf-8")

    def test_eks_reuses_sanitized_aws_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); self.snapshot(root, "aws:///us-east-1a/i-redacted", "arn:aws:eks:us-east-1:000000000000:cluster/demo")
            aws = {"readOnly": True, "state": "AVAILABLE", "safety": {"requests": 8}, "coverage": {"cluster": {"state": "AVAILABLE"}}, "inventory": {"cluster": {"version": "1.35", "platformVersion": "eks.42", "supportType": "STANDARD", "oidcConfigured": True, "zones": ["a", "b"]}, "addons": [{"name": "coredns", "status": "ACTIVE"}], "podIdentityAssociations": [], "network": {"subnets": [{"availableIpv4": 100, "availablePercent": 50}]}}}
            (root / "aws-eks-assessment.json").write_text(json.dumps(aws), encoding="utf-8")
            value = cloud.generate(root)
        self.assertEqual(value["provider"], "eks")
        self.assertEqual(value["state"], "AVAILABLE")
        self.assertEqual(value["safety"]["mutations"], 0)
        self.assertTrue(all(item["status"] != "UNKNOWN" for item in value["bestPractices"]))

    def test_cloud_command_allowlist_rejects_mutations_before_execution(self) -> None:
        with patch.object(cloud.subprocess, "run") as run:
            payload, state, reason = cloud.command_json(["az", "aks", "delete", "--name", "blocked"])
        self.assertEqual(payload, {})
        self.assertEqual(state, "UNAVAILABLE")
        self.assertIn("allowlist read-only", reason)
        run.assert_not_called()

    def test_aks_collects_only_normalized_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); self.snapshot(root, "azure:///subscriptions/private/resource", "aks-demo")
            show = {"id": "/subscriptions/private/resourceGroups/private/providers/Microsoft.ContainerService/managedClusters/demo", "location": "brazilsouth", "kubernetesVersion": "1.35.4", "securityProfile": {"workloadIdentity": {"enabled": True}}, "oidcIssuerProfile": {"enabled": True, "issuerUrl": "https://private"}, "aadProfile": {"enableAzureRbac": True, "tenantId": "private"}, "autoUpgradeProfile": {"upgradeChannel": "stable"}, "apiServerAccessProfile": {"enablePrivateCluster": True}, "networkProfile": {"networkPlugin": "azure", "outboundType": "userDefinedRouting"}}
            responses = [
                (show, "AVAILABLE", ""),
                ({"controlPlaneProfile": {"upgrades": [{"kubernetesVersion": "1.36.1"}]}}, "AVAILABLE", ""),
                ({"items": [{"name": "system", "mode": "System"}, {"name": "apps", "mode": "User"}]}, "AVAILABLE", ""),
                ({"values": [{"version": "1.35.4"}, {"version": "1.36.1"}]}, "AVAILABLE", ""),
            ]
            env = {"AKS_CLUSTER_NAME": "demo", "AKS_RESOURCE_GROUP": "private-rg"}
            with patch.dict(os.environ, env, clear=False), patch.object(cloud, "command_json", side_effect=responses):
                value = cloud.generate(root)
            serialized = json.dumps(value)
        self.assertEqual(value["provider"], "aks")
        self.assertEqual(value["state"], "AVAILABLE")
        self.assertEqual(value["lifecycle"]["supportState"], "PROVIDER_AVAILABLE")
        self.assertNotIn("subscriptions/private", serialized)
        self.assertNotIn("private-rg", serialized)
        self.assertNotIn("tenantId", serialized)

    def test_aks_versions_accept_current_patch_map_and_legacy_shapes(self) -> None:
        current = {"values": [{"version": "1.35", "patchVersions": {"1.35.4": {}}}]}
        legacy = {"orchestrators": [{"orchestratorVersion": "1.34.7"}]}
        unknown = {"values": [{"isPreview": False}]}
        self.assertEqual(cloud.aks_available_versions(current), ["1.35", "1.35.4"])
        self.assertEqual(cloud.aks_available_versions(legacy), ["1.34.7"])
        self.assertEqual(cloud.aks_available_versions(unknown), [])

    def test_azure_role_supports_current_and_legacy_version_reads_only(self) -> None:
        role_path = Path(__file__).resolve().parents[1] / "deploy" / "azure-aks-assessment-readonly-role.json"
        actions = json.loads(role_path.read_text(encoding="utf-8"))["Actions"]
        self.assertIn("Microsoft.ContainerService/locations/kubernetesversions/read", actions)
        self.assertIn("Microsoft.ContainerService/locations/orchestrators/read", actions)
        self.assertTrue(all(action.lower().endswith("/read") for action in actions))

    def test_gke_uses_describe_and_server_config_without_persisting_project(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); self.snapshot(root, "gce://private/zone/node", "gke_private-project_us-central1_demo")
            show = {"currentMasterVersion": "1.35.5-gke.1", "currentNodeVersion": "1.35.5-gke.1", "location": "us-central1", "releaseChannel": {"channel": "REGULAR"}, "workloadIdentityConfig": {"workloadPool": "private.svc.id.goog"}, "shieldedNodes": {"enabled": True}, "privateClusterConfig": {"enablePrivateNodes": True}, "networkPolicy": {"enabled": True}, "endpoint": "private"}
            config = {"validMasterVersions": ["1.35.5-gke.1", "1.36.1-gke.1"]}
            with patch.object(cloud, "command_json", side_effect=[(show, "AVAILABLE", ""), (config, "AVAILABLE", "")]):
                value = cloud.generate(root)
            serialized = json.dumps(value)
        self.assertEqual(value["provider"], "gke")
        self.assertEqual(value["state"], "AVAILABLE")
        self.assertTrue(all(item["status"] == "PASS" for item in value["bestPractices"]))
        self.assertNotIn("private-project", serialized)
        self.assertNotIn('"endpoint":', serialized)

    def test_generic_cluster_is_not_applicable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); self.snapshot(root, "", "kind-lab")
            value = cloud.generate(root)
        self.assertEqual(value["provider"], "generic-kubernetes")
        self.assertEqual(value["state"], "N/A")
        self.assertFalse(value["bestPractices"])


if __name__ == "__main__":
    unittest.main()
