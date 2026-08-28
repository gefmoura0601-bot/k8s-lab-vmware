#!/usr/bin/env python3
"""Portability and safety tests for automatic Prometheus runtime discovery."""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import prometheus_telemetry as telemetry


class PrometheusTelemetryTests(unittest.TestCase):
    @staticmethod
    def discovery_with_series(metric: str, labels: dict[str, str]) -> telemetry.MetricDiscovery:
        discovery = object.__new__(telemetry.MetricDiscovery)
        discovery.base_url = "http://prometheus.invalid"
        discovery.start = 0
        discovery.end = 1
        discovery._lock = threading.Lock()
        discovery._series_cache = {metric: [labels]}
        discovery._selector_cache = {}
        discovery.series_errors = []
        discovery.catalog_source = "test"
        discovery.catalog_reason = ""
        discovery.catalog = {metric}
        return discovery

    def test_alternate_kubernetes_labels_are_discovered_by_values(self) -> None:
        metric = "otel_dotnet_gc_heap_used_bytes"
        discovery = self.discovery_with_series(
            metric,
            {
                "__name__": metric,
                "k8s_namespace_name": "tenant-blue",
                "k8s_pod_name": "orders-engine-7b67dbcd58-z9x8q",
                "app_kubernetes_io_name": "orders-engine",
                "memory_type": "managed",
            },
        )
        target = telemetry.WorkloadTarget("tenant-blue", "orders-engine")
        match = discovery.selector_for(metric, target, "heap_used", False)
        self.assertIsNotNone(match)
        assert match is not None
        self.assertIn('k8s_namespace_name="tenant-blue"', match.selector)
        self.assertIn('k8s_pod_name=~"orders-engine-.*"', match.selector)
        self.assertIn('memory_type="managed"', match.selector)
        self.assertEqual(match.matched_by, "pod-prefix")

    def test_loopback_and_link_local_prometheus_destinations_are_rejected(self) -> None:
        with patch.object(telemetry.socket, "getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1", 9090))]):
            with self.assertRaises(telemetry.TelemetryError):
                telemetry.validate_url("http://prometheus.example:9090")
        with patch.object(telemetry.socket, "getaddrinfo", return_value=[(2, 1, 6, "", ("169.254.169.254", 80))]):
            with self.assertRaises(telemetry.TelemetryError):
                telemetry.validate_url("http://metadata.example")

    def test_prometheus_allowlist_is_enforced(self) -> None:
        address = [(2, 1, 6, "", ("10.0.0.10", 9090))]
        with patch.dict(telemetry.os.environ, {"PROMETHEUS_ALLOWED_HOSTS": "approved.example"}), patch.object(
            telemetry.socket, "getaddrinfo", return_value=address
        ):
            self.assertEqual("http://approved.example:9090", telemetry.validate_url("http://approved.example:9090"))
            with self.assertRaises(telemetry.TelemetryError):
                telemetry.validate_url("http://unapproved.example:9090")

    def test_runtime_is_derived_from_arbitrary_manifest_content(self) -> None:
        dotnet = {
            "name": "backend",
            "image": "registry.example/platform/component:42",
            "env": [
                {"name": "DOTNET_EnableDiagnostics_IPC", "value": "1"},
                {"name": "COMPlus_PerfMapEnabled", "value": "1"},
            ],
        }
        java = {
            "name": "worker",
            "image": "registry.example/platform/worker:42",
            "command": ["java", "-jar", "/app/service.jar"],
            "env": [{"name": "JAVA_TOOL_OPTIONS", "value": "-XX:+UseG1GC"}],
        }
        self.assertEqual(telemetry.container_runtimes(dotnet), {".NET"})
        self.assertEqual(telemetry.container_runtimes(java), {"JVM"})

    def test_workloads_file_keeps_safe_tuning_and_redacts_sensitive_values(self) -> None:
        payload = {
            "items": [
                {
                    "kind": "Deployment",
                    "metadata": {"namespace": "tenant-green", "name": "payments-engine"},
                    "spec": {
                        "template": {
                            "spec": {
                                "containers": [
                                    {
                                        "name": "api",
                                        "image": "registry.example/api:1",
                                        "env": [
                                            {"name": "DOTNET_EnableDiagnostics_Profiler", "value": "1"},
                                            {"name": "CORECLR_PROFILER_PATH", "value": "/opt/profiler.so"},
                                            {"name": "DOTNET_API_KEY", "value": "must-not-leak"},
                                            {"name": "UNRELATED_SETTING", "value": "not-collected"},
                                        ],
                                    }
                                ]
                            }
                        }
                    },
                }
            ]
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "workloads.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            targets = telemetry.workloads_file(str(path))
        self.assertEqual(len(targets), 1)
        target = targets[0]
        self.assertEqual((target.namespace, target.deployment), ("tenant-green", "payments-engine"))
        self.assertEqual(target.runtime_hints, (".NET",))
        config = {name: value for _, name, value in target.runtime_config}
        self.assertEqual(config["DOTNET_EnableDiagnostics_Profiler"], "1")
        self.assertEqual(config["CORECLR_PROFILER_PATH"], "/opt/profiler.so")
        self.assertEqual(config["DOTNET_API_KEY"], "<redacted>")
        self.assertNotIn("UNRELATED_SETTING", config)

    def test_technology_is_detected_from_arbitrary_manifest(self) -> None:
        rabbit = {
            "name": "broker",
            "image": "registry.example/messaging/rabbitmq:3.13",
            "env": [],
        }
        nginx = {
            "name": "edge",
            "image": "registry.example/nginx:1.27",
            "env": [],
        }
        self.assertEqual(telemetry.container_technologies(rabbit), {"RabbitMQ"})
        self.assertEqual(telemetry.container_technologies(nginx), {"NGINX"})

    def test_rabbitmq_metric_is_correlated_without_lab_specific_names(self) -> None:
        metric = "rabbitmq_queue_messages_ready"
        discovery = self.discovery_with_series(
            metric,
            {
                "__name__": metric,
                "namespace": "tenant-messaging",
                "pod": "broker-service-6b9f7d8b8c-ab123",
            },
        )
        target = telemetry.WorkloadTarget("tenant-messaging", "broker-service")
        candidates = telemetry.candidate_map(discovery)
        technologies, bindings, missing = telemetry.technology_bindings(
            discovery, target, candidates
        )
        self.assertIn("RabbitMQ", technologies)
        self.assertIn("rabbitmq_messages_ready", bindings)
        self.assertNotIn("rabbitmq_messages_ready", missing)

    def test_simple_metrics_are_human_readable(self) -> None:
        rows = telemetry.simple_metrics(
            {
                "cpu": {
                    "state": "AVAILABLE",
                    "unit": "cores",
                    "mean": 0.2,
                    "p95": 0.5,
                    "peak": 0.8,
                }
            }
        )
        self.assertEqual(rows[0]["p95"], "50.0% de 1 vCPU")
        self.assertEqual(rows[0]["assessment"], "evidência disponível")
    def test_embedded_credentials_are_rejected(self) -> None:
        with self.assertRaises(telemetry.TelemetryError):
            telemetry.validate_url("https://user:password@prometheus.example")


if __name__ == "__main__":
    unittest.main()
