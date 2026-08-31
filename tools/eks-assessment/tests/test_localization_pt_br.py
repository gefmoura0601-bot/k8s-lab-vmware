#!/usr/bin/env python3
"""Regressão da localização visual sem traduzir identificadores técnicos."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from localization_pt_br import localize_finding, pt_text


class LocalizationPtBrTests(unittest.TestCase):
    def test_localizes_human_text_and_preserves_machine_fields(self) -> None:
        source = {
            "severity": "WARN", "status": "OPEN", "category": "Security",
            "check": "NetworkPolicy coverage",
            "detail": "Namespace has workloads but no NetworkPolicy object.",
            "recommendation": "Start with default-deny ingress/egress and allow only required flows.",
            "namespace": "banking", "workload": "Deployment/account-service",
            "ruleId": "K8S-NETPOL-001", "fingerprint": "abc123",
        }

        localized = localize_finding(source)

        self.assertEqual("WARN", localized["severity"])
        self.assertEqual("OPEN", localized["status"])
        self.assertEqual("K8S-NETPOL-001", localized["ruleId"])
        self.assertEqual("Deployment/account-service", localized["workload"])
        self.assertEqual("Segurança", localized["category"])
        self.assertIn("NetworkPolicy", localized["check"])
        self.assertIn("Namespace", localized["detail"])
        self.assertIn("default-deny", localized["recommendation"])

    def test_unknown_technical_text_is_not_modified(self) -> None:
        value = "HPA targets Deployment/api with Ready Pods"
        self.assertEqual(value, pt_text(value))

    def test_rbac_terms_remain_in_english(self) -> None:
        value = pt_text("Replace wildcards with exact API groups, resources, resourceNames and verbs.")
        self.assertEqual("Remova wildcards e limite as permissões aos apiGroups, resources, resourceNames e verbs estritamente necessários.", value)
        self.assertNotIn("verbos", value)


if __name__ == "__main__":
    unittest.main()
