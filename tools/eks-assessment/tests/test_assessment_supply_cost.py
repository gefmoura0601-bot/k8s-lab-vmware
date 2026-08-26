#!/usr/bin/env python3
"""Tests for optional supply-chain, DR and cost assessment rules."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import aws_eks_assessment
from eks_semantic_assessment import SemanticRules


class FakeAssessment:
    def __init__(self, directory: Path):
        self.directory = directory
        self.collection = {"resources": {}, "budget": {}}
        self.workloads: list[dict] = []
        self.findings: list[dict] = []
        self.base = {
            "pods": {"items": [{"metadata": {"namespace": "payments", "name": "api"}, "spec": {"nodeName": "spot-a", "containers": [{"name": "api", "resources": {"requests": {"cpu": "100m", "memory": "128Mi"}}}]}}]},
            "pvcs": {"items": [{"metadata": {"namespace": "payments", "name": "old-data"}, "status": {"phase": "Bound"}}]},
            "nodes": {"items": [{"metadata": {"name": "spot-a", "labels": {"eks.amazonaws.com/capacityType": "SPOT"}}, "status": {"allocatable": {"cpu": "4", "memory": "8Gi"}}}]},
        }
        self.raw = {
            "persistentvolumes": {"items": [{"metadata": {"name": "released-pv"}, "status": {"phase": "Released"}}]},
            "services": {"items": [{"metadata": {"namespace": "payments", "name": "unused-lb"}, "spec": {"type": "LoadBalancer", "selector": {"app": "missing"}}}]},
            "endpointslices": {"items": []},
            "karpenter_nodepools": {"items": []},
        }

    def workload_objects(self):
        return []

    def add(self, *args, **kwargs):
        self.findings.append({"severity": args[0], "ruleId": kwargs.get("rule_id"), "detail": args[3]})


class SupplyAndCostTests(unittest.TestCase):
    def test_ecr_images_are_discovered_without_exporting_credentials(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "workloads.json"
            path.write_text(json.dumps({"items": [{"spec": {"template": {"spec": {"containers": [{"image": "123456789012.dkr.ecr.us-east-1.amazonaws.com/cards/api:1.2.3"}]}}}}]}), encoding="utf-8")
            images = aws_eks_assessment.workload_images(path)
            self.assertEqual(1, len(images))
            self.assertIn("cards/api:1.2.3", next(iter(images)))

    def test_cost_rules_find_storage_lb_and_spot_risks(self):
        with tempfile.TemporaryDirectory() as temp:
            assessment = FakeAssessment(Path(temp))
            SemanticRules(assessment).cost_and_capacity_efficiency()
            rule_ids = {item["ruleId"] for item in assessment.findings}
            self.assertIn("k8s.cost.pvc-unreferenced", rule_ids)
            self.assertIn("k8s.cost.pv-released", rule_ids)
            self.assertIn("k8s.cost.loadbalancer-endpoints", rule_ids)
            self.assertIn("k8s.reliability.spot-interruption", rule_ids)


if __name__ == "__main__":
    unittest.main()
