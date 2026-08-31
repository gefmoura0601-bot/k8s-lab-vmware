#!/usr/bin/env python3
"""Authentication tests for the remotely exposed assessment dashboard."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "src" / "assessment_dashboard.py"
STATIC = ROOT / "web" / "public"
sys.path.insert(0, str(ROOT / "src"))

import assessment_dashboard as dashboard


class DashboardAccessTests(unittest.TestCase):
    def test_remote_binding_requires_access_token(self) -> None:
        result = subprocess.run(
            [sys.executable, str(DASHBOARD), "--root", str(ROOT / "tests"), "--static", str(STATIC),
             "--host", "0.0.0.0", "--port", "8765", "--allow-remote"],
            text=True, capture_output=True, timeout=10, check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("requires an access token", result.stderr)

    @staticmethod
    def handler(path: str, cookie: str = ""):
        instance = object.__new__(dashboard.Handler)
        instance.path = path
        instance.headers = {"Cookie": cookie}
        instance.access_token = "a" * 43
        instance.response_status = None
        instance.response_headers = []
        instance.send_response = lambda status: setattr(instance, "response_status", status)
        instance.send_header = lambda name, value: instance.response_headers.append((name, value))
        instance.end_headers = lambda: None
        instance.send_html = lambda _value, status=200: setattr(instance, "response_status", status)
        return instance

    def test_token_is_exchanged_for_http_only_session_cookie(self) -> None:
        token = "a" * 43
        login = self.handler(f"/?access_token={token}")
        self.assertFalse(login.authenticated())
        self.assertEqual(login.response_status, 303)
        self.assertIn(("Location", "/"), login.response_headers)
        cookie = next(value for name, value in login.response_headers if name == "Set-Cookie")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)

        session = self.handler("/api/health", f"assessment_session={token}")
        self.assertTrue(session.authenticated())

        denied = self.handler("/api/health")
        self.assertFalse(denied.authenticated())
        self.assertEqual(denied.response_status, 401)

    def test_grouped_navigation_and_global_search_do_not_index_log_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            collection = root / "eks-20260830-search"
            collection.mkdir()
            (collection / "metadata.json").write_text(json.dumps({"clusterName": "lab", "context": "kind-lab", "createdAt": "2026-08-30T00:00:00Z"}), encoding="utf-8")
            (collection / "comprehensive-assessment.json").write_text(json.dumps({"summary": {}, "findings": [], "technologies": [], "capacityRecommendations": []}), encoding="utf-8")
            operational = {"bestPractices": {"rules": [{"ruleId": "bestpractice.gke.release.channel", "status": "PASS", "recommendation": "Manter release channel."}]}, "logs": {"entries": [{"target": "apps/deployment/api", "state": "COLLECTED", "content": "never-index-this-secret"}]}}
            (collection / "operational-insights.json").write_text(json.dumps(operational), encoding="utf-8")
            handler = object.__new__(dashboard.Handler)
            handler.root = root
            handler.static = STATIC
            navigation = handler.layout("Teste", "body", collection, "best")
            self.assertIn("ANÁLISE", navigation)
            self.assertIn("OPERAÇÕES", navigation)
            self.assertIn("BUSCA GLOBAL", navigation)
            result = handler.search_page(collection, {"q": ["bestpractice.gke"]})
            self.assertIn("bestpractice.gke.release.channel", result)
            hidden = handler.search_page(collection, {"q": ["never-index-this-secret"]})
            self.assertIn("Nenhum resultado", hidden)
            self.assertNotIn("never-index-this-secret</td>", hidden)

    def test_overview_explains_critical_state_and_renders_node_health(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            collection = root / "eks-20260831-node-health"
            collection.mkdir()
            (collection / "metadata.json").write_text(json.dumps({"clusterName": "lab", "createdAt": "2026-08-31T00:00:00Z"}), encoding="utf-8")
            (collection / "nodes.json").write_text(json.dumps({"items": [{"metadata": {"name": "node-a", "labels": {}}, "status": {"conditions": [{"type": "Ready", "status": "True"}], "nodeInfo": {"kubeletVersion": "v1.35.0"}}}]}), encoding="utf-8")
            finding = {"severity": "CRIT", "category": "Security", "check": "Privileged container", "namespace": "apps", "workload": "Deployment/api", "detail": "privileged=true", "recommendation": "Remover privilégio."}
            comprehensive = {"summary": {"checks": 1, "workloads": 1, "containers": 1, "passed": 0, "notApplicable": 0}, "findings": [finding], "technologies": [], "capacityRecommendations": []}
            (collection / "comprehensive-assessment.json").write_text(json.dumps(comprehensive), encoding="utf-8")
            node_health = {
                "state": "PASS",
                "notice": "Uso observado vem da Metrics API. Node overhead / não atribuído inclui OS e runtime.",
                "summary": {"nodes": 1, "critical": 0, "warnings": 0, "partial": 0, "passed": 1, "metricsNodes": 1, "metricsCoveragePercent": 100.0},
                "items": [{
                    "node": "node-a", "state": "PASS", "ready": True, "pressureConditions": [], "diagnosis": ["Dentro dos thresholds"],
                    "runtime": "containerd://2", "os": "Linux", "allocatable": {"cpuCores": 4, "memoryBytes": 8589934592, "pods": 110}, "nodeReserve": {"cpuCores": 0.2, "memoryBytes": 268435456},
                    "usage": {"cpu": {"value": 1, "percent": 25}, "memory": {"value": 2147483648, "percent": 25}, "pods": {"value": 10, "percent": 9.1}, "requests": {"cpuCores": 2, "memoryBytes": 3221225472, "cpuPercent": 50, "memoryPercent": 37.5}, "breakdown": {"kubernetesPods": {"cpuCores": 0.1, "memoryBytes": 134217728}, "daemonSets": {"cpuCores": 0.1, "memoryBytes": 134217728}, "workloads": {"cpuCores": 0.5, "memoryBytes": 1073741824}, "nodeOverheadUnattributed": {"cpuCores": 0.3, "memoryBytes": 805306368}, "headroom": {"cpuCores": 3, "memoryBytes": 6442450944}}},
                    "evidence": {"metrics": "MetricsAPI", "runningPodsObserved": 10, "runningPodsExpected": 10},
                }],
            }
            operational = {"nodeHealth": node_health, "bestPractices": {"rules": []}, "logs": {"entries": []}}
            (collection / "operational-insights.json").write_text(json.dumps(operational), encoding="utf-8")
            handler = object.__new__(dashboard.Handler)
            handler.root = root
            handler.static = STATIC
            overview = handler.overview(collection)
            self.assertIn("Estado CRÍTICO porque 1 finding(s)", overview)
            self.assertIn("não significa necessariamente indisponibilidade total", overview)
            self.assertIn("Ver findings críticos", overview)
            self.assertIn("/node-health?collection=eks-20260831-node-health", overview)
            page = handler.node_health(collection)
            self.assertIn("Node Health", page)
            self.assertIn("Node overhead / não atribuído", page)
            self.assertIn("containerd://2", page)
            nodes_page = handler.resources_page(collection, {"kind": ["nodes"]})
            self.assertIn("Node Health", nodes_page)
            self.assertIn("25%", nodes_page)


if __name__ == "__main__":
    unittest.main()
