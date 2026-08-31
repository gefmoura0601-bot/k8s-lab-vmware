#!/usr/bin/env python3
"""Adaptive, read-only Kubernetes/EKS assessment over persisted snapshots.

The collector intentionally uses only Python's standard library and kubectl GET
operations.  Secret values and arbitrary environment values are never copied to
the generated application-manifest evidence.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import math
import os
import random
import re
import subprocess
import sys
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterable

try:
    import resource as process_resource
except ImportError:  # pragma: no cover - assessment runtime is Linux
    process_resource = None

from eks_semantic_assessment import apply_semantic_assessment
from cis_security_assessment import generate as generate_cis_security
from operational_insights import generate as generate_operational_insights
from cloud_provider_assessment import generate as generate_cloud_provider_assessment


SCHEMA_VERSION = "4.0"
SYSTEM_NAMESPACES = {
    "kube-system", "kube-public", "kube-node-lease", "calico-system",
    "calico-apiserver", "tigera-operator", "monitoring", "observability",
    "argocd", "istio-system", "cert-manager", "ingress-nginx",
}
SAFE_RUNTIME_ENV = {
    "JAVA_OPTS", "JAVA_TOOL_OPTIONS", "JDK_JAVA_OPTIONS", "CATALINA_OPTS",
    "KAFKA_HEAP_OPTS", "KAFKA_JVM_PERFORMANCE_OPTS", "KAFKA_OPTS",
    "DOTNET_GCHeapHardLimit", "DOTNET_GCHeapHardLimitPercent",
    "DOTNET_GCConserveMemory", "DOTNET_gcServer", "COMPlus_gcServer",
    "COMPlus_GCHeapHardLimit", "COMPlus_GCHeapHardLimitPercent",
    "DOTNET_EnableDiagnostics", "ASPNETCORE_ENVIRONMENT",
    "RABBITMQ_VM_MEMORY_HIGH_WATERMARK", "RABBITMQ_SERVER_ADDITIONAL_ERL_ARGS",
    "NGINX_ENTRYPOINT_QUIET_LOGS",
}
SAFE_RUNTIME_ENV_UPPER = {name.upper() for name in SAFE_RUNTIME_ENV}
SAFE_RUNTIME_ENV_PREFIXES = ("DOTNET_", "COMPLUS_", "CORECLR_", "MONO_", "ASPNETCORE_")
SENSITIVE = re.compile(r"(?i)(password|passwd|token|secret|credential|private.?key|api.?key|client.?secret|connection.?string)")
SOURCE_URLS = {
    "resources": "https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/",
    "security": "https://kubernetes.io/docs/concepts/security/pod-security-standards/",
    "topology": "https://kubernetes.io/docs/concepts/scheduling-eviction/topology-spread-constraints/",
    "pdb": "https://kubernetes.io/docs/tasks/run-application/configure-pdb/",
    "hpa": "https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/",
    "keda": "https://keda.sh/docs/latest/reference/scaledobject-spec/",
    "network": "https://kubernetes.io/docs/concepts/services-networking/network-policies/",
    "probes": "https://kubernetes.io/docs/concepts/configuration/liveness-readiness-startup-probes/",
    "java": "https://docs.oracle.com/en/java/javase/",
    "dotnet": "https://learn.microsoft.com/dotnet/core/runtime-config/garbage-collector",
    "rabbitmq": "https://www.rabbitmq.com/docs/production-checklist",
    "kafka": "https://kafka.apache.org/documentation/",
    "nginx": "https://nginx.org/en/docs/",
    "gateway": "https://gateway-api.sigs.k8s.io/",
    "eks": "https://docs.aws.amazon.com/eks/latest/best-practices/introduction.html",
}


# filename, kubectl API-resource candidates, namespaced.  The list mirrors the
# official sample discovery tool while keeping one bounded GET per resource.
RESOURCE_SPECS: dict[str, tuple[str, tuple[str, ...], bool]] = {
    "jobs": ("jobs.json", ("jobs.batch", "jobs"), True),
    "cronjobs": ("cronjobs.json", ("cronjobs.batch", "cronjobs"), True),
    "replicasets": ("replicasets.json", ("replicasets.apps", "replicasets"), True),
    "services": ("services.json", ("services",), True),
    "endpointslices": ("endpointslices.json", ("endpointslices.discovery.k8s.io", "endpointslices"), True),
    "ingresses": ("ingresses.json", ("ingresses.networking.k8s.io", "ingresses"), True),
    "hpas": ("hpas.json", ("horizontalpodautoscalers.autoscaling", "horizontalpodautoscalers"), True),
    "vpas": ("vpas.json", ("verticalpodautoscalers.autoscaling.k8s.io",), True),
    "keda_scaledobjects": ("keda-scaledobjects.json", ("scaledobjects.keda.sh",), True),
    "keda_scaledjobs": ("keda-scaledjobs.json", ("scaledjobs.keda.sh",), True),
    "pdbs": ("poddisruptionbudgets.json", ("poddisruptionbudgets.policy", "poddisruptionbudgets"), True),
    "networkpolicies": ("networkpolicies.json", ("networkpolicies.networking.k8s.io", "networkpolicies"), True),
    "resourcequotas": ("resourcequotas.json", ("resourcequotas",), True),
    "limitranges": ("limitranges.json", ("limitranges",), True),
    "serviceaccounts": ("serviceaccounts.json", ("serviceaccounts",), True),
    "roles": ("roles.json", ("roles.rbac.authorization.k8s.io", "roles"), True),
    "rolebindings": ("rolebindings.json", ("rolebindings.rbac.authorization.k8s.io", "rolebindings"), True),
    "clusterroles": ("clusterroles.json", ("clusterroles.rbac.authorization.k8s.io", "clusterroles"), False),
    "clusterrolebindings": ("clusterrolebindings.json", ("clusterrolebindings.rbac.authorization.k8s.io", "clusterrolebindings"), False),
    "storageclasses": ("storageclasses.json", ("storageclasses.storage.k8s.io", "storageclasses"), False),
    "persistentvolumes": ("persistentvolumes.json", ("persistentvolumes",), False),
    "pvcs": ("pvcs.json", ("persistentvolumeclaims",), True),
    "crds": ("crds.json", ("customresourcedefinitions.apiextensions.k8s.io", "customresourcedefinitions"), False),
    "apiservices": ("apiservices.json", ("apiservices.apiregistration.k8s.io", "apiservices"), False),
    "validatingwebhooks": ("validatingwebhooks.json", ("validatingwebhookconfigurations.admissionregistration.k8s.io",), False),
    "mutatingwebhooks": ("mutatingwebhooks.json", ("mutatingwebhookconfigurations.admissionregistration.k8s.io",), False),
    "gatewayclasses": ("gatewayclasses.json", ("gatewayclasses.gateway.networking.k8s.io",), False),
    "gateways": ("gateways.json", ("gateways.gateway.networking.k8s.io",), True),
    "httproutes": ("httproutes.json", ("httproutes.gateway.networking.k8s.io",), True),
    "grpcroutes": ("grpcroutes.json", ("grpcroutes.gateway.networking.k8s.io",), True),
    "tlsroutes": ("tlsroutes.json", ("tlsroutes.gateway.networking.k8s.io",), True),
    "tcproutes": ("tcproutes.json", ("tcproutes.gateway.networking.k8s.io",), True),
    "udproutes": ("udproutes.json", ("udproutes.gateway.networking.k8s.io",), True),
    "rollouts": ("rollouts.json", ("rollouts.argoproj.io",), True),
    "servicemonitors": ("servicemonitors.json", ("servicemonitors.monitoring.coreos.com",), True),
    "podmonitors": ("podmonitors.json", ("podmonitors.monitoring.coreos.com",), True),
    "prometheusrules": ("prometheusrules.json", ("prometheusrules.monitoring.coreos.com",), True),
    "karpenter_nodepools": ("karpenter-nodepools.json", ("nodepools.karpenter.sh",), False),
    "karpenter_nodeclaims": ("karpenter-nodeclaims.json", ("nodeclaims.karpenter.sh",), False),
    "karpenter_ec2nodeclasses": ("karpenter-ec2nodeclasses.json", ("ec2nodeclasses.karpenter.k8s.aws",), False),
    "istio_virtualservices": ("istio-virtualservices.json", ("virtualservices.networking.istio.io",), True),
    "istio_destinationrules": ("istio-destinationrules.json", ("destinationrules.networking.istio.io",), True),
    "istio_peerauthentications": ("istio-peerauthentications.json", ("peerauthentications.security.istio.io",), True),
    "kyverno_clusterpolicies": ("kyverno-clusterpolicies.json", ("clusterpolicies.kyverno.io",), False),
    "argocd_applications": ("argocd-applications.json", ("applications.argoproj.io",), True),
    "volumesnapshots": ("volumesnapshots.json", ("volumesnapshots.snapshot.storage.k8s.io",), True),
    "volumesnapshotclasses": ("volumesnapshotclasses.json", ("volumesnapshotclasses.snapshot.storage.k8s.io",), False),
    "volumesnapshotcontents": ("volumesnapshotcontents.json", ("volumesnapshotcontents.snapshot.storage.k8s.io",), False),
    "priorityclasses": ("priorityclasses.json", ("priorityclasses.scheduling.k8s.io",), False),
    "runtimeclasses": ("runtimeclasses.json", ("runtimeclasses.node.k8s.io",), False),
    "csidrivers": ("csidrivers.json", ("csidrivers.storage.k8s.io",), False),
    "csinodes": ("csinodes.json", ("csinodes.storage.k8s.io",), False),
    "volumeattachments": ("volumeattachments.json", ("volumeattachments.storage.k8s.io",), False),
    "velero_backups": ("velero-backups.json", ("backups.velero.io",), True),
    "velero_restores": ("velero-restores.json", ("restores.velero.io",), True),
    "velero_schedules": ("velero-schedules.json", ("schedules.velero.io",), True),
    "external_secrets": ("external-secrets.json", ("externalsecrets.external-secrets.io",), True),
    "secret_stores": ("secretstores.json", ("secretstores.external-secrets.io",), True),
    "cluster_secret_stores": ("clustersecretstores.json", ("clustersecretstores.external-secrets.io",), False),
    "certificates": ("certificates.json", ("certificates.cert-manager.io",), True),
    "issuers": ("issuers.json", ("issuers.cert-manager.io",), True),
    "cluster_issuers": ("clusterissuers.json", ("clusterissuers.cert-manager.io",), False),
    "strimzi_kafkas": ("strimzi-kafkas.json", ("kafkas.kafka.strimzi.io",), True),
    "strimzi_kafkatopics": ("strimzi-kafkatopics.json", ("kafkatopics.kafka.strimzi.io",), True),
    "rabbitmq_clusters": ("rabbitmq-clusters.json", ("rabbitmqclusters.rabbitmq.com",), True),
    "cnpg_clusters": ("cnpg-clusters.json", ("clusters.postgresql.cnpg.io",), True),
    "policyreports": ("policyreports.json", ("policyreports.wgpolicyk8s.io",), True),
    "clusterpolicyreports": ("clusterpolicyreports.json", ("clusterpolicyreports.wgpolicyk8s.io",), False),
}
SENSITIVE_API_RESOURCES = {"secrets", "configmaps"}

TECH_PATTERNS = {
    "Java": re.compile(r"(?i)(openjdk|temurin|corretto|java\b|\.jar\b|spring|quarkus|wildfly|jboss|tomcat|keycloak)"),
    ".NET": re.compile(r"(?i)(dotnet|aspnet|\.dll\b|mcr\.microsoft\.com/dotnet)"),
    "Kafka": re.compile(r"(?i)(kafka|strimzi|confluent)"),
    "RabbitMQ": re.compile(r"(?i)(rabbitmq|rabbit[-_]?mq)"),
    "Nginx": re.compile(r"(?i)(nginx|openresty)"),
    "API Gateway": re.compile(r"(?i)(kong|apisix|traefik|envoy|gateway|ingress[-_]?nginx|aws-load-balancer-controller)"),
    "PostgreSQL": re.compile(r"(?i)(postgres|postgresql|cloudnative-pg|cnpg)"),
    "Redis": re.compile(r"(?i)(redis|valkey)"),
}


def utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def peak_rss_bytes() -> int | None:
    if process_resource is None:
        return None
    value = int(process_resource.getrusage(process_resource.RUSAGE_SELF).ru_maxrss)
    return value if sys.platform == "darwin" else value * 1024


def finding_quality(findings: list[dict[str, Any]]) -> dict[str, Any]:
    by_fingerprint: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for finding in findings:
        by_fingerprint[str(finding.get("fingerprint") or "")].append(finding)
    duplicates = sum(len(values) - 1 for key, values in by_fingerprint.items() if key and len(values) > 1)
    conflicts = sum(len({str(item.get("severity")) for item in values}) > 1 for key, values in by_fingerprint.items() if key)
    low_confidence_pass = sum(item.get("severity") == "PASS" and item.get("confidence") == "LOW" for item in findings)
    rule_counts = Counter(str(item.get("ruleId") or "UNKNOWN") for item in findings)
    threshold = max(20, round(len(findings) * 0.08))
    review = [{"ruleId": rule_id, "findings": count, "reason": "high-volume rule; calibrate against expected policy and workload ownership"} for rule_id, count in rule_counts.most_common() if count >= threshold][:20]
    return {
        "state": "PASS" if not (duplicates or conflicts or low_confidence_pass) else "WARN",
        "stableIdentityDuplicates": duplicates,
        "conflictingSeverities": conflicts,
        "lowConfidencePasses": low_confidence_pass,
        "highVolumeThreshold": threshold,
        "falsePositiveReviewCandidates": review,
    }


def load_json(path: Path, fallback: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return fallback if fallback is not None else {"items": []}


def items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict) and isinstance(value.get("items"), list):
        return [x for x in value["items"] if isinstance(x, dict)]
    return []


def run(command: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, text=True, capture_output=True, check=False, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        return subprocess.CompletedProcess(command, 124, "", str(error))


def collection_identity(namespace: str) -> dict[str, str]:
    context = run(["kubectl", "config", "current-context"], 10).stdout.strip()
    server = run(
        ["kubectl", "config", "view", "--minify", "-o", "jsonpath={.clusters[0].cluster.server}"],
        10,
    ).stdout.strip()
    return {
        "context": context,
        "serverHash": hashlib.sha256(server.encode()).hexdigest() if server else "",
        "namespaceScope": namespace or "*",
    }


def snapshot_hashes(directory: Path) -> dict[str, str]:
    names = {value[0] for value in RESOURCE_SPECS.values()} | {
        "nodes.json", "pods.json", "node-metrics.json", "pod-metrics.json", "workloads.json", "namespaces.json", "events.json",
        "universal-inventory.json", "api-resources.json",
    }
    return {
        name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
        for name in sorted(names)
        if (directory / name).is_file()
    }


def write_collection_provenance(directory: Path, namespace: str) -> None:
    value = {
        "schemaVersion": SCHEMA_VERSION,
        **collection_identity(namespace),
        "snapshots": snapshot_hashes(directory),
    }
    (directory / "collection-provenance.json").write_text(
        json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def valid_resume_provenance(directory: Path, namespace: str) -> tuple[bool, str]:
    provenance = load_json(directory / "collection-provenance.json", {})
    if not isinstance(provenance, dict) or provenance.get("schemaVersion") != SCHEMA_VERSION:
        return False, "resume provenance is absent or uses another schema version"
    expected = collection_identity(namespace)
    for key, value in expected.items():
        if not value or provenance.get(key) != value:
            return False, f"resume provenance mismatch: {key}"
    recorded = provenance.get("snapshots") or {}
    if not isinstance(recorded, dict) or not recorded:
        return False, "resume provenance has no snapshot hashes"
    for name, digest in recorded.items():
        path = directory / str(name)
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            return False, f"resume snapshot integrity mismatch: {name}"
    return True, ""


def clean_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(metadata)
    for key in ("managedFields", "resourceVersion", "uid", "generation", "creationTimestamp", "selfLink"):
        result.pop(key, None)
    annotations = result.get("annotations")
    if isinstance(annotations, dict):
        result["annotations"] = {
            key: "<redacted>" if SENSITIVE.search(key) else value
            for key, value in annotations.items()
        }
    return result


def safe_runtime_env_name(name: str) -> bool:
    upper = name.upper()
    return upper in SAFE_RUNTIME_ENV_UPPER or upper.startswith(SAFE_RUNTIME_ENV_PREFIXES)


def safe_runtime_env_value(name: str, entry: dict[str, Any]) -> str:
    if "value" not in entry:
        return "<valueFrom>"
    value = str(entry.get("value", ""))
    if SENSITIVE.search(name) or SENSITIVE.search(value):
        return "<redacted>"
    return value[:600] + ("..." if len(value) > 600 else "")


def sanitize_env(env: list[Any]) -> list[Any]:
    result: list[Any] = []
    for raw in env:
        if not isinstance(raw, dict):
            continue
        entry = copy.deepcopy(raw)
        name = str(entry.get("name", ""))
        if "value" in entry:
            entry["value"] = (
                safe_runtime_env_value(name, entry)
                if safe_runtime_env_name(name)
                else "<redacted>"
            )
        result.append(entry)
    return result


def sanitize_tree(value: Any, parent: str = "") -> Any:
    if isinstance(value, list):
        return [sanitize_tree(x, parent) for x in value]
    if not isinstance(value, dict):
        return value
    result: dict[str, Any] = {}
    for key, child in value.items():
        if key == "managedFields" or key == "status":
            continue
        if key == "metadata" and isinstance(child, dict):
            result[key] = clean_metadata(child)
        elif key == "env" and isinstance(child, list):
            result[key] = sanitize_env(child)
        elif key in {"data", "stringData", "binaryData"}:
            if isinstance(child, dict):
                result[f"{key}Keys"] = sorted(child)
            else:
                result[key] = "<redacted>"
        elif SENSITIVE.search(key) and key not in {"secretName", "secretKeyRef", "imagePullSecrets"}:
            result[key] = "<redacted>"
        else:
            result[key] = sanitize_tree(child, key)
    return result


def sanitize_snapshot_tree(value: Any, parent: str = "") -> Any:
    """Redact persisted snapshots while retaining health/status fields used by the UI."""
    if isinstance(value, list):
        return [sanitize_snapshot_tree(x, parent) for x in value]
    if not isinstance(value, dict):
        return value
    result: dict[str, Any] = {}
    for key, child in value.items():
        if key == "managedFields":
            continue
        if key == "metadata" and isinstance(child, dict):
            result[key] = clean_metadata(child)
        elif key == "env" and isinstance(child, list):
            result[key] = sanitize_env(child)
        elif key in {"data", "stringData", "binaryData"}:
            if isinstance(child, dict):
                result[f"{key}Keys"] = sorted(child)
            else:
                result[key] = "<redacted>"
        elif SENSITIVE.search(key) and key not in {"secretName", "secretKeyRef", "imagePullSecrets"}:
            result[key] = "<redacted>"
        else:
            result[key] = sanitize_snapshot_tree(child, key)
    return result


def sanitize_events(value: dict[str, Any]) -> dict[str, Any]:
    """Keep event classification/timestamps but omit free-form messages and object UIDs."""
    clean: dict[str, Any] = {
        "apiVersion": value.get("apiVersion", "v1"),
        "kind": value.get("kind", "EventList"),
        "items": [],
    }
    for event in items(value):
        involved = event.get("involvedObject") or event.get("regarding") or {}
        row: dict[str, Any] = {
            "apiVersion": event.get("apiVersion"),
            "kind": event.get("kind", "Event"),
            "metadata": clean_metadata(event.get("metadata", {})),
            "involvedObject": {
                key: involved.get(key)
                for key in ("apiVersion", "kind", "namespace", "name", "fieldPath")
                if involved.get(key) is not None
            },
        }
        for key in (
            "type", "reason", "action", "reportingController", "reportingInstance",
            "count", "eventTime", "firstTimestamp", "lastTimestamp", "deprecatedCount",
        ):
            if event.get(key) is not None:
                row[key] = sanitize_snapshot_tree(event[key], key)
        if isinstance(event.get("series"), dict):
            row["series"] = {
                key: event["series"].get(key)
                for key in ("count", "lastObservedTime")
                if event["series"].get(key) is not None
            }
        clean["items"].append(row)
    return clean

def sanitize_resource_list(resource_key: str, value: dict[str, Any]) -> dict[str, Any]:
    clean = {"apiVersion": value.get("apiVersion", "v1"), "kind": value.get("kind", "List"), "items": []}
    for item in items(value):
        if resource_key == "crds":
            spec = item.get("spec") or {}
            clean["items"].append({
                "apiVersion": item.get("apiVersion"), "kind": item.get("kind", "CustomResourceDefinition"),
                "metadata": clean_metadata(item.get("metadata", {})),
                "spec": {"group": spec.get("group"), "scope": spec.get("scope"), "names": spec.get("names"),
                         "versions": [{"name": x.get("name"), "served": x.get("served"), "storage": x.get("storage")} for x in spec.get("versions") or []]},
            })
        else:
            # Persist operational status for dashboard/drift analysis. The separate
            # application-manifests artifact intentionally removes status.
            clean["items"].append(sanitize_snapshot_tree(item))
    return clean


class CollectionBudgetExceeded(RuntimeError):
    pass


class ApiBudget:
    """Thread-safe request, time and response-size budget for production clusters."""

    def __init__(self, max_requests: int, max_duration: int, max_bytes: int, delay_ms: int):
        self.max_requests = max_requests
        self.max_duration = max_duration
        self.max_bytes = max_bytes
        self.delay = delay_ms / 1000.0
        self.started = time.monotonic()
        self.requests = 0
        self.retries = 0
        self.response_bytes = 0
        self.throttles = 0
        self.reason = ""
        self._next_allowed = self.started
        self._lock = threading.Lock()

    def before_request(self, retry: bool = False) -> None:
        with self._lock:
            elapsed = time.monotonic() - self.started
            if self.reason:
                raise CollectionBudgetExceeded(self.reason)
            if self.requests >= self.max_requests:
                self.reason = f"request budget exhausted ({self.max_requests})"
                raise CollectionBudgetExceeded(self.reason)
            if elapsed >= self.max_duration:
                self.reason = f"duration budget exhausted ({self.max_duration}s)"
                raise CollectionBudgetExceeded(self.reason)
            now = time.monotonic()
            wait = max(0.0, self._next_allowed - now)
            self._next_allowed = max(now, self._next_allowed) + self.delay
            self.requests += 1
            if retry:
                self.retries += 1
        if wait:
            time.sleep(wait)

    def after_response(self, response: subprocess.CompletedProcess[str]) -> None:
        size = len((response.stdout or "").encode("utf-8", "replace")) + len((response.stderr or "").encode("utf-8", "replace"))
        stderr = (response.stderr or "").lower()
        with self._lock:
            self.response_bytes += size
            if "too many requests" in stderr or "429" in stderr or "throttl" in stderr:
                self.throttles += 1
            if self.response_bytes > self.max_bytes and not self.reason:
                self.reason = f"response budget exhausted ({self.max_bytes} bytes)"

    def summary(self) -> dict[str, Any]:
        return {
            "requests": self.requests, "retries": self.retries,
            "responseBytes": self.response_bytes, "throttles": self.throttles,
            "elapsedSeconds": round(time.monotonic() - self.started, 3),
            "limits": {"requests": self.max_requests, "durationSeconds": self.max_duration,
                       "responseBytes": self.max_bytes, "delayMs": int(self.delay * 1000)},
            "state": "PARTIAL" if self.reason else "AVAILABLE", "reason": self.reason,
        }


TRANSIENT_KUBECTL = re.compile(r"(?i)(too many requests|\b429\b|\b5\d\d\b|timeout|temporar|connection reset|server is currently unable)")


def run_kubectl(command: list[str], timeout: int, budget: ApiBudget, retries: int) -> subprocess.CompletedProcess[str]:
    last = subprocess.CompletedProcess(command, 75, "", "collection did not run")
    for attempt in range(retries + 1):
        try:
            budget.before_request(retry=attempt > 0)
        except CollectionBudgetExceeded as error:
            return subprocess.CompletedProcess(command, 75, "", str(error))
        last = run(command, timeout)
        budget.after_response(last)
        if last.returncode == 0 or attempt >= retries or not TRANSIENT_KUBECTL.search(last.stderr or ""):
            return last
        time.sleep(min(10.0, (2 ** attempt) + random.random()))
    return last

def api_resources(timeout: int, budget: ApiBudget, retries: int) -> tuple[set[str], set[str], dict[str, Any]]:
    result: dict[str, Any] = {"state": "AVAILABLE", "namespaced": [], "cluster": []}
    discovered: list[set[str]] = []
    for flag, key in (("true", "namespaced"), ("false", "cluster")):
        response = run_kubectl(["kubectl", "api-resources", "--verbs=list", f"--namespaced={flag}", "-o", "name"], timeout, budget, retries)
        if response.returncode:
            result["state"] = "UNAVAILABLE"
            result.setdefault("errors", []).append(response.stderr.strip() or f"api-resources {flag} failed")
            discovered.append(set())
        else:
            names = sorted({line.strip() for line in response.stdout.splitlines() if line.strip()})
            result[key] = names
            discovered.append(set(names))
    return discovered[0], discovered[1], result


def choose_resource(candidates: Iterable[str], available: set[str]) -> str | None:
    for candidate in candidates:
        if candidate in available:
            return candidate
    # kubectl sometimes reports a short name while the spec uses a qualified one.
    by_short = {name.split(".", 1)[0]: name for name in available}
    for candidate in candidates:
        if "." not in candidate and candidate in by_short:
            return by_short[candidate]
    return None


def collect_universal_inventory(directory: Path, namespaced: set[str], cluster: set[str],
                                raw: dict[str, dict[str, Any]], coverage: dict[str, Any],
                                timeout: int, chunk_size: int, workers: int, budget: ApiBudget,
                                retries: int, namespace: str = "", resume: bool = False) -> dict[str, Any]:
    """Inventory every listable API; unknown resources persist identity only."""
    inventory_path = directory / "universal-inventory.json"
    if resume and inventory_path.is_file():
        previous = load_json(inventory_path, {})
        if isinstance(previous, dict) and isinstance(previous.get("resources"), list):
            return {key: value for key, value in previous.items() if key != "resources"} | {"resumed": True}
    entries: list[dict[str, Any]] = []
    covered: set[str] = set()
    for key, result in coverage.items():
        resource = result.get("resource")
        if not resource:
            continue
        covered.add(resource)
        objects = []
        for item in items(raw.get(key)):
            metadata = item.get("metadata") or {}
            objects.append({
                "apiVersion": item.get("apiVersion"), "kind": item.get("kind"),
                "namespace": metadata.get("namespace", "-"), "name": metadata.get("name", ""),
            })
        entries.append({"resource": resource, "scope": "namespaced" if RESOURCE_SPECS[key][2] else "cluster",
                        "state": result.get("state"), "count": len(objects), "deepCollected": True,
                        "objects": objects, "reason": result.get("reason", "")})

    skipped_sensitive = sorted(resource for resource in namespaced if resource.split(".", 1)[0] in SENSITIVE_API_RESOURCES)
    entries.extend(
        {
            "resource": resource,
            "scope": "namespaced",
            "state": "PARTIAL",
            "count": 0,
            "deepCollected": False,
            "objects": [],
            "reason": "collection disabled by data-minimization policy",
        }
        for resource in skipped_sensitive
    )
    targets = [
        (resource, "namespaced")
        for resource in namespaced
        if resource not in covered
        and resource not in skipped_sensitive
        and "/" not in resource
    ]
    targets += [(resource, "cluster") for resource in cluster if resource not in covered and "/" not in resource]

    def collect_one(target: tuple[str, str]) -> dict[str, Any]:
        resource, scope = target
        command = ["kubectl", "get", resource]
        if scope == "namespaced":
            command += ["-n", namespace] if namespace else ["-A"]
        command += ["-o=custom-columns=NAMESPACE:.metadata.namespace,NAME:.metadata.name,KIND:.kind,APIVERSION:.apiVersion",
                    "--no-headers", f"--chunk-size={chunk_size}", f"--request-timeout={timeout}s"]
        response = run_kubectl(command, max(timeout * 2, 30), budget, retries)
        objects = []
        if response.returncode == 0:
            for line in response.stdout.splitlines():
                columns = line.split()
                if len(columns) >= 4:
                    object_namespace, name, kind, api_version = columns[:4]
                    objects.append({"apiVersion": api_version, "kind": kind,
                                    "namespace": "-" if object_namespace == "<none>" else object_namespace, "name": name})
        return {"resource": resource, "scope": scope,
                "state": "AVAILABLE" if response.returncode == 0 else "UNAVAILABLE",
                "count": len(objects), "deepCollected": False, "objects": objects,
                "reason": "" if response.returncode == 0 else (response.stderr.strip() or "list failed")[:500]}

    if targets:
        with ThreadPoolExecutor(max_workers=min(workers, len(targets))) as executor:
            futures = [executor.submit(collect_one, target) for target in targets]
            entries.extend(future.result() for future in as_completed(futures))
    entries.sort(key=lambda value: (value["scope"], value["resource"]))
    unavailable = sum(value["state"] == "UNAVAILABLE" for value in entries)
    partial = sum(value["state"] == "PARTIAL" for value in entries)
    output = {"schemaVersion": SCHEMA_VERSION, "generatedAt": utcnow(),
              "state": "PARTIAL" if unavailable or partial else "AVAILABLE", "resourceTypes": len(entries),
              "objectCount": sum(value["count"] for value in entries),
              "deepCollectedResourceTypes": sum(bool(value["deepCollected"]) for value in entries),
              "identityOnlyResourceTypes": sum(not value["deepCollected"] for value in entries),
              "unavailableResourceTypes": unavailable, "partialResourceTypes": partial, "resources": entries}
    (directory / "universal-inventory.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    return {key: value for key, value in output.items() if key != "resources"}

def collect_live(directory: Path, timeout: int, chunk_size: int, inventory_workers: int,
                 budget: ApiBudget, retries: int, namespace: str = "",
                 resume: bool = False) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    namespaced, cluster, api_inventory = api_resources(timeout, budget, retries)
    (directory / "api-resources.json").write_text(json.dumps(api_inventory, ensure_ascii=False, indent=2), encoding="utf-8")
    raw: dict[str, dict[str, Any]] = {}
    coverage: dict[str, Any] = {}
    for key, (filename, candidates, is_namespaced) in RESOURCE_SPECS.items():
        available = namespaced if is_namespaced else cluster
        resource = choose_resource(candidates, available)
        path = directory / filename
        if resume and resource and path.is_file():
            cached = load_json(path, None)
            if isinstance(cached, dict) and isinstance(cached.get("items"), list):
                raw[key] = cached
                coverage[key] = {"state": "AVAILABLE", "count": len(items(cached)), "resource": resource, "resumed": True}
                continue
        if not resource:
            payload = {"apiVersion": "v1", "kind": "List", "items": []}
            path.write_text(json.dumps(payload), encoding="utf-8")
            raw[key] = payload
            discovery_available = api_inventory.get("state") == "AVAILABLE"
            coverage[key] = {
                "state": "N/A" if discovery_available else "PARTIAL",
                "count": 0,
                "reason": "API resource not served" if discovery_available else "API discovery unavailable; applicability is unknown",
            }
            continue
        command = ["kubectl", "get", resource]
        if is_namespaced:
            command += ["-n", namespace] if namespace else ["-A"]
        command += ["-o", "json", f"--chunk-size={chunk_size}", f"--request-timeout={timeout}s"]
        response = run_kubectl(command, max(timeout * 3, 60), budget, retries)
        try:
            payload = json.loads(response.stdout) if response.returncode == 0 else {"items": []}
        except json.JSONDecodeError:
            payload = {"items": []}
            response = subprocess.CompletedProcess(command, 1, response.stdout, "kubectl returned invalid JSON")
        raw[key] = payload
        if response.returncode:
            coverage[key] = {"state": "UNAVAILABLE", "count": 0, "resource": resource, "reason": response.stderr.strip()[:500]}
        else:
            coverage[key] = {"state": "AVAILABLE", "count": len(items(payload)), "resource": resource}
        path.write_text(json.dumps(sanitize_resource_list(key, payload), ensure_ascii=False, indent=2), encoding="utf-8")
    universal = collect_universal_inventory(directory, namespaced, cluster, raw, coverage, timeout, chunk_size, inventory_workers, budget, retries, namespace, resume)
    return raw, {"apiResources": api_inventory, "resources": coverage, "universalInventory": universal, "requestBudget": budget.summary(), "namespaceScope": namespace or "all"}


def existing_resources(directory: Path) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    raw: dict[str, dict[str, Any]] = {}
    coverage: dict[str, Any] = {}
    previous = load_json(directory / "comprehensive-assessment.json", {})
    previous_resources = ((previous.get("collection") or {}).get("resources") or {}) if isinstance(previous, dict) else {}
    for key, (filename, _candidates, _namespaced) in RESOURCE_SPECS.items():
        payload = load_json(directory / filename, {"items": []})
        raw[key] = payload
        old = previous_resources.get(key) or {}
        state = old.get("state") or ("AVAILABLE" if (directory / filename).exists() else "UNKNOWN")
        coverage[key] = {**old, "state": state, "count": len(items(payload))}
        if state == "UNKNOWN": coverage[key].setdefault("reason", "snapshot absent; applicability was not proven")
    universal = load_json(directory / "universal-inventory.json", {"state": "UNKNOWN", "resourceTypes": 0, "objectCount": 0})
    universal = {key: value for key, value in universal.items() if key != "resources"}
    return raw, {"apiResources": load_json(directory / "api-resources.json", {"state": "UNKNOWN"}), "resources": coverage, "universalInventory": universal}

def pod_template(item: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    kind = str(item.get("kind", ""))
    spec = item.get("spec") or {}
    if kind == "Pod":
        return item.get("metadata") or {}, spec
    if kind == "CronJob":
        template = (((spec.get("jobTemplate") or {}).get("spec") or {}).get("template") or {})
    else:
        template = spec.get("template") or {}
    return template.get("metadata") or {}, template.get("spec") or {}


def desired_replicas(item: dict[str, Any]) -> int:
    kind = str(item.get("kind", ""))
    spec, status = item.get("spec") or {}, item.get("status") or {}
    if kind == "DaemonSet":
        return int(status.get("desiredNumberScheduled") or 0)
    if kind == "Job":
        return int(spec.get("parallelism") or spec.get("completions") or 1)
    if kind == "CronJob":
        return 0
    return int(spec.get("replicas") if spec.get("replicas") is not None else 1)


def ready_replicas(item: dict[str, Any]) -> int:
    status = item.get("status") or {}
    if item.get("kind") == "Pod":
        statuses = status.get("containerStatuses") or []
        return int(bool(statuses) and all(x.get("ready") for x in statuses))
    return int(status.get("readyReplicas") or status.get("numberReady") or status.get("availableReplicas") or 0)


def selector_matches(selector: dict[str, Any], labels: dict[str, str]) -> bool:
    if not selector:
        return False
    for key, value in (selector.get("matchLabels") or {}).items():
        if labels.get(key) != value:
            return False
    for expression in selector.get("matchExpressions") or []:
        key, operator, values = expression.get("key"), expression.get("operator"), expression.get("values") or []
        present = key in labels
        if operator == "In" and (not present or labels.get(key) not in values): return False
        if operator == "NotIn" and present and labels.get(key) in values: return False
        if operator == "Exists" and not present: return False
        if operator == "DoesNotExist" and present: return False
    return True


def cpu_cores(value: Any) -> float | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)([num]?)", text)
    if not match:
        return None
    number, suffix = float(match.group(1)), match.group(2)
    return number * {"": 1.0, "m": 1e-3, "u": 1e-6, "n": 1e-9}[suffix]


def memory_bytes(value: Any) -> float | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)([EPTGMK]i?|[eptgmk])?", text)
    if not match:
        return None
    number, suffix = float(match.group(1)), match.group(2) or ""
    binary = {"Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "Ti": 2**40, "Pi": 2**50, "Ei": 2**60}
    decimal = {"k": 1e3, "K": 1e3, "m": 1e-3, "M": 1e6, "g": 1e9, "G": 1e9, "t": 1e12, "T": 1e12, "p": 1e15, "P": 1e15, "e": 1e18, "E": 1e18, "": 1}
    return number * (binary.get(suffix) or decimal.get(suffix, 1))


def fmt_cpu(value: float) -> str:
    return f"{max(1, math.ceil(value * 1000))}m" if value < 1 else f"{value:.2f}".rstrip("0").rstrip(".")


def fmt_memory(value: float) -> str:
    if value >= 2**30:
        return f"{math.ceil(value / 2**30 * 10) / 10:g}Gi"
    return f"{max(1, math.ceil(value / 2**20))}Mi"


def runtime_env(container: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for entry in container.get("env") or []:
        name = str(entry.get("name", ""))
        if safe_runtime_env_name(name):
            result[name] = safe_runtime_env_value(name, entry)
    return result


def detect_technologies(item: dict[str, Any], container: dict[str, Any]) -> list[str]:
    metadata = item.get("metadata") or {}
    safe = runtime_env(container)
    text = " ".join([
        str(metadata.get("name", "")), str(metadata.get("namespace", "")),
        str(container.get("name", "")), str(container.get("image", "")),
        " ".join(map(str, container.get("command") or [])),
        " ".join(map(str, container.get("args") or [])),
        " ".join(safe), " ".join(safe.values()),
    ])
    return [name for name, pattern in TECH_PATTERNS.items() if pattern.search(text)]


class Assessment:
    def __init__(self, directory: Path, raw: dict[str, dict[str, Any]], collection: dict[str, Any]):
        self.directory = directory
        self.raw = raw
        self.collection = collection
        self.findings: list[dict[str, Any]] = []
        self.workloads: list[dict[str, Any]] = []
        self.technology_workloads: dict[str, set[str]] = defaultdict(set)
        self.capacity: list[dict[str, Any]] = []
        self.sanitized_snapshots: list[str] = []
        self.semantic_summary: dict[str, Any] = {}
        self.aws_eks = load_json(directory / "aws-eks-assessment.json", {})
        self.base = {
            "nodes": load_json(directory / "nodes.json", {"items": []}),
            "pods": load_json(directory / "pods.json", {"items": []}),
            "workloads": load_json(directory / "workloads.json", {"items": []}),
            "namespaces": load_json(directory / "namespaces.json", {"items": []}),
            "events": load_json(directory / "events.json", {"items": []}),
            "pvcs": load_json(directory / "pvcs.json", {"items": []}),
        }

    def add(self, severity: str, category: str, check: str, detail: str, recommendation: str = "",
            namespace: str = "-", workload: str = "-", container: str = "-",
            source: str = "", technology: str = "", evidence: str = "snapshot",
            rule_id: str = "", confidence: str = "HIGH", applicability: str = "APPLICABLE") -> None:
        status_map = {
            "CRIT": "OPEN", "WARN": "OPEN", "INFO": "REVIEW", "PASS": "COMPLIANT",
            "N/A": "NOT_APPLICABLE", "UNKNOWN": "UNKNOWN", "PARTIAL": "PARTIAL",
        }
        if severity not in status_map:
            raise ValueError(f"unsupported finding severity: {severity}")
        slug = lambda value: re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
        canonical_rule = rule_id or f"k8s.{slug(category)}.{slug(check)}"
        resource_key = "|".join((namespace or "-", workload or "-", container or "-"))
        finding_id = hashlib.sha256(f"{canonical_rule}|{resource_key}".encode()).hexdigest()[:16]
        evidence_hash = hashlib.sha256(detail.encode()).hexdigest()[:16]
        if severity == "N/A":
            applicability = "NOT_APPLICABLE"
        if severity in {"UNKNOWN", "PARTIAL"} and confidence == "HIGH":
            confidence = "LOW"
        self.findings.append({
            "id": finding_id, "fingerprint": finding_id, "ruleId": canonical_rule,
            "resourceKey": resource_key, "evidenceHash": evidence_hash,
            "severity": severity, "status": status_map[severity], "category": category,
            "check": check, "namespace": namespace, "workload": workload,
            "container": container, "detail": detail, "recommendation": recommendation,
            "technology": technology, "evidence": evidence,
            "confidence": confidence, "applicability": applicability,
            "source": source or SOURCE_URLS.get(category.lower(), ""),
        })

    def workload_objects(self) -> list[dict[str, Any]]:
        result = list(items(self.base["workloads"]))
        result += items(self.raw.get("jobs")) + items(self.raw.get("cronjobs")) + items(self.raw.get("rollouts"))
        result += [pod for pod in items(self.base["pods"]) if not (pod.get("metadata") or {}).get("ownerReferences")]
        # ReplicaSets and controller-owned Pods are inventory-only because their PodSpec is
        # already represented by the owning workload.
        return result

    def integrate_aws_eks(self) -> None:
        """Merge the independent AWS/EKS evidence without changing K8s gates."""
        if not isinstance(self.aws_eks, dict) or not self.aws_eks:
            self.add(
                "UNKNOWN", "EKS", "AWS/EKS assessment artifact",
                "aws-eks-assessment.json was not produced; Kubernetes evidence remains valid.",
                "Run aws_eks_assessment.py with explicit read-only AWS access when this is an EKS cluster.",
                evidence="collector-artifact", rule_id="eks.collection.artifact", confidence="LOW",
                applicability="UNKNOWN",
            )
            return
        existing = {item.get("fingerprint") or item.get("id") for item in self.findings}
        for finding in self.aws_eks.get("findings") or []:
            if not isinstance(finding, dict):
                continue
            fingerprint = finding.get("fingerprint") or finding.get("id")
            if fingerprint and fingerprint in existing:
                continue
            self.findings.append(copy.deepcopy(finding))
            if fingerprint:
                existing.add(fingerprint)

    def cluster_health(self) -> None:
        nodes, pods = items(self.base["nodes"]), items(self.base["pods"])
        if not nodes:
            self.add("WARN", "Health", "Node inventory", "Node snapshot unavailable or empty.", "Verify kubectl access and collection log.")
        else:
            unready = []
            zones: set[str] = set()
            for node in nodes:
                name = (node.get("metadata") or {}).get("name", "")
                labels = (node.get("metadata") or {}).get("labels") or {}
                zone = labels.get("topology.kubernetes.io/zone") or labels.get("failure-domain.beta.kubernetes.io/zone")
                if zone: zones.add(zone)
                ready = any(c.get("type") == "Ready" and c.get("status") == "True" for c in (node.get("status") or {}).get("conditions") or [])
                if not ready: unready.append(name)
            self.add("CRIT" if unready else "PASS", "Health", "Nodes Ready", f"{len(nodes)-len(unready)}/{len(nodes)} Ready" + (f"; NotReady={','.join(unready)}" if unready else ""), "Investigate kubelet, runtime, CNI, pressure and recent events." if unready else "")
            self.add("PASS" if len(zones) >= 2 else "WARN", "Topology", "Failure domains", f"Detected zones: {', '.join(sorted(zones)) or 'none'}", "For production, distribute nodes and replicas across independent failure domains.", source=SOURCE_URLS["topology"])
        problematic = []
        for pod in pods:
            status = pod.get("status") or {}
            phase = status.get("phase", "Unknown")
            waiting = [((c.get("state") or {}).get("waiting") or {}).get("reason") for c in status.get("containerStatuses") or []]
            restarts = sum(int(c.get("restartCount") or 0) for c in status.get("containerStatuses") or [])
            if phase not in {"Running", "Succeeded"} or any(waiting) or restarts >= 5:
                problematic.append((pod, phase, next((x for x in waiting if x), phase), restarts))
        self.add("PASS" if not problematic else "WARN", "Health", "Pod health", f"{len(problematic)} problematic pod(s) out of {len(pods)}", "Review events, probes, logs, resources and dependencies.")
        for pod, phase, reason, restarts in problematic[:500]:
            metadata = pod.get("metadata") or {}
            severity = "CRIT" if reason in {"CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull", "CreateContainerConfigError"} else "WARN"
            self.add(severity, "PodHealth", "Pod state", f"phase={phase}; reason={reason}; restarts={restarts}", "Review describe/events, container logs, probes, image and resource pressure.", metadata.get("namespace", "-"), f"Pod/{metadata.get('name','-')}")

    def policy_maps(self) -> dict[str, Any]:
        hpa_targets: dict[tuple[str, str, str], dict[str, Any]] = {}
        for hpa in items(self.raw.get("hpas")):
            meta, target = hpa.get("metadata") or {}, (hpa.get("spec") or {}).get("scaleTargetRef") or {}
            hpa_targets[(meta.get("namespace", "default"), str(target.get("kind", "Deployment")).lower(), target.get("name", ""))] = hpa
        keda_targets: dict[tuple[str, str, str], dict[str, Any]] = {}
        for scaled in items(self.raw.get("keda_scaledobjects")):
            meta, target = scaled.get("metadata") or {}, (scaled.get("spec") or {}).get("scaleTargetRef") or {}
            keda_targets[(meta.get("namespace", "default"), str(target.get("kind", "Deployment")).lower(), target.get("name", ""))] = scaled
        pdbs = items(self.raw.get("pdbs"))
        policies_by_ns = Counter((x.get("metadata") or {}).get("namespace", "default") for x in items(self.raw.get("networkpolicies")))
        quotas_by_ns = Counter((x.get("metadata") or {}).get("namespace", "default") for x in items(self.raw.get("resourcequotas")))
        limits_by_ns = Counter((x.get("metadata") or {}).get("namespace", "default") for x in items(self.raw.get("limitranges")))
        return {"hpa": hpa_targets, "keda": keda_targets, "pdbs": pdbs, "network": policies_by_ns, "quotas": quotas_by_ns, "limits": limits_by_ns}

    def analyze_container_security(self, container: dict[str, Any], podspec: dict[str, Any], namespace: str, ref: str, cname: str) -> None:
        security = container.get("securityContext") or {}
        pod_security = podspec.get("securityContext") or {}
        if security.get("privileged") is True:
            self.add("CRIT", "Security", "Privileged container", "securityContext.privileged=true", "Remove privileged mode or document a narrowly scoped exception.", namespace, ref, cname, SOURCE_URLS["security"])
        if security.get("runAsUser") == 0:
            self.add("CRIT", "Security", "Root user", "runAsUser=0", "Run as a non-root UID and set runAsNonRoot=true.", namespace, ref, cname, SOURCE_URLS["security"])
        elif security.get("runAsNonRoot") is not True and pod_security.get("runAsNonRoot") is not True:
            self.add("WARN", "Security", "Non-root enforcement", "runAsNonRoot is not explicitly true.", "Set runAsNonRoot=true and use a non-zero UID supported by the image.", namespace, ref, cname, SOURCE_URLS["security"])
        if security.get("allowPrivilegeEscalation") is not False:
            self.add("WARN", "Security", "Privilege escalation", "allowPrivilegeEscalation is not explicitly false.", "Set allowPrivilegeEscalation=false unless technically required.", namespace, ref, cname, SOURCE_URLS["security"])
        dropped = set(((security.get("capabilities") or {}).get("drop") or []))
        if "ALL" not in dropped:
            self.add("INFO", "Security", "Linux capabilities", "Capabilities do not explicitly drop ALL.", "Drop ALL and add back only capabilities proven necessary.", namespace, ref, cname, SOURCE_URLS["security"])
        if security.get("readOnlyRootFilesystem") is not True:
            self.add("INFO", "Security", "Read-only root filesystem", "readOnlyRootFilesystem is not true.", "Enable it when compatible and mount explicit writable paths.", namespace, ref, cname, SOURCE_URLS["security"])

    def analyze_workloads(self) -> None:
        maps = self.policy_maps()
        for item in self.workload_objects():
            kind = str(item.get("kind", "Workload"))
            metadata = item.get("metadata") or {}
            namespace, name = metadata.get("namespace", "default"), metadata.get("name", "-")
            ref = f"{kind}/{name}"
            template_meta, podspec = pod_template(item)
            labels = template_meta.get("labels") or {}
            replicas, ready = desired_replicas(item), ready_replicas(item)
            target_key = (namespace, kind.lower(), name)
            hpa, keda = maps["hpa"].get(target_key), maps["keda"].get(target_key)
            matching_pdb = next((p for p in maps["pdbs"] if (p.get("metadata") or {}).get("namespace", "default") == namespace and selector_matches((p.get("spec") or {}).get("selector") or {}, labels)), None)
            containers = list(podspec.get("containers") or [])
            init_containers = list(podspec.get("initContainers") or [])
            technologies: set[str] = set()
            container_rows: list[dict[str, Any]] = []
            ephemeral_containers = list(podspec.get("ephemeralContainers") or [])
            for container_type, container_list in (("app", containers), ("init", init_containers), ("ephemeral", ephemeral_containers)):
                for container in container_list:
                    cname, image = container.get("name", "-"), container.get("image", "")
                    detected = detect_technologies(item, container)
                    technologies.update(detected)
                    for tech in detected: self.technology_workloads[tech].add(f"{namespace}/{ref}:{cname}")
                    resources = container.get("resources") or {}
                    requests, limits = resources.get("requests") or {}, resources.get("limits") or {}
                    container_rows.append({
                        "name": cname, "type": container_type, "image": image,
                        "technologies": detected, "runtimeOptions": runtime_env(container),
                        "requests": requests, "limits": limits,
                    })
                    if container_type == "app":
                        missing_req = [x for x in ("cpu", "memory") if not requests.get(x)]
                        missing_lim = [x for x in ("cpu", "memory") if not limits.get(x)]
                        if missing_req:
                            self.add("WARN", "Resources", "Resource requests", f"Missing {', '.join(missing_req)} request(s).", "Define requests from p90 usage plus validated headroom; requests drive scheduling and HPA utilization.", namespace, ref, cname, SOURCE_URLS["resources"])
                        else:
                            self.add("PASS", "Resources", "Resource requests", f"cpu={requests.get('cpu')}; memory={requests.get('memory')}", namespace=namespace, workload=ref, container=cname, source=SOURCE_URLS["resources"])
                        if missing_lim:
                            self.add("WARN", "Resources", "Resource limits", f"Missing {', '.join(missing_lim)} limit(s).", "Set a memory limit from observed p99 plus native/cache headroom; evaluate CPU limits against throttling policy.", namespace, ref, cname, SOURCE_URLS["resources"])
                        else:
                            self.add("PASS", "Resources", "Resource limits", f"cpu={limits.get('cpu')}; memory={limits.get('memory')}", namespace=namespace, workload=ref, container=cname, source=SOURCE_URLS["resources"])
                        if not container.get("readinessProbe"):
                            self.add("WARN", "Reliability", "Readiness probe", "Readiness probe is absent.", "Add a dependency-aware readiness probe so traffic is sent only to ready instances.", namespace, ref, cname, SOURCE_URLS["probes"])
                        if not container.get("livenessProbe"):
                            self.add("INFO", "Reliability", "Liveness probe", "Liveness probe is absent.", "Add only when a reliable deadlock/failure signal exists; avoid restart loops.", namespace, ref, cname, SOURCE_URLS["probes"])
                    self.analyze_container_security(container, podspec, namespace, ref, cname)
                    if container_type == "init" and (not requests.get("cpu") or not requests.get("memory")):
                        self.add("INFO", "Resources", "Init container resources", "Init container lacks a complete CPU/memory request.", "Size init-container requests because Kubernetes scheduling uses the highest init requirement.", namespace, ref, cname, SOURCE_URLS["resources"])
                    if not image or image.endswith(":latest") or (":" not in image.rsplit("/", 1)[-1] and "@sha256:" not in image):
                        self.add("WARN", "SupplyChain", "Mutable image", f"image={image or 'empty'}", "Pin an immutable version or image digest and validate provenance/signature.", namespace, ref, cname)
                    elif "@sha256:" not in image:
                        self.add("INFO", "SupplyChain", "Image digest", f"Tagged image {image}", "For high-assurance releases, pin and attest an immutable digest.", namespace, ref, cname)
            host_flags = [name for name in ("hostNetwork", "hostPID", "hostIPC") if podspec.get(name) is True]
            host_paths = [v.get("name", "-") for v in podspec.get("volumes") or [] if v.get("hostPath")]
            if host_flags:
                self.add("CRIT", "Security", "Host namespace sharing", f"Enabled: {', '.join(host_flags)}", "Remove host namespace sharing or document a controlled infrastructure exception.", namespace, ref, source=SOURCE_URLS["security"])
            if host_paths:
                self.add("CRIT", "Security", "hostPath volumes", f"Volumes: {', '.join(host_paths)}", "Replace hostPath with a constrained CSI/PVC volume or document the node-level exception.", namespace, ref, source=SOURCE_URLS["security"])
            seccomp = (podspec.get("securityContext") or {}).get("seccompProfile") or {}
            if seccomp.get("type") not in {"RuntimeDefault", "Localhost"}:
                self.add("WARN", "Security", "Seccomp", "Pod does not explicitly select RuntimeDefault/Localhost.", "Set seccompProfile.type=RuntimeDefault at Pod level.", namespace, ref, source=SOURCE_URLS["security"])
            if podspec.get("serviceAccountName", "default") == "default":
                self.add("INFO", "Security", "Service account", "Uses the default ServiceAccount.", "Use a dedicated ServiceAccount and least-privilege RBAC; disable token automount when API access is unnecessary.", namespace, ref, source=SOURCE_URLS["security"])
            long_running = kind in {"Deployment", "StatefulSet", "Rollout"}
            if long_running:
                if replicas < 2 and not hpa and not keda:
                    self.add("WARN", "Availability", "Replica redundancy", f"replicas={replicas}; no HPA/KEDA target", "Use at least two replicas where the application supports it, then validate failure-domain placement.", namespace, ref)
                elif replicas >= 2:
                    self.add("PASS", "Availability", "Replica redundancy", f"replicas={replicas}; ready={ready}", namespace=namespace, workload=ref)
                spread = podspec.get("topologySpreadConstraints") or []
                anti = ((podspec.get("affinity") or {}).get("podAntiAffinity") or {})
                if replicas >= 2 and not spread and not anti:
                    self.add("WARN", "Topology", "Replica spreading", "No topologySpreadConstraints or podAntiAffinity.", "Spread replicas by zone and hostname with selectors matching the workload.", namespace, ref, source=SOURCE_URLS["topology"])
                elif replicas >= 2:
                    self.add("PASS", "Topology", "Replica spreading", f"topologySpreadConstraints={len(spread)}; antiAffinity={bool(anti)}", namespace=namespace, workload=ref, source=SOURCE_URLS["topology"])
                if replicas >= 2 and not matching_pdb:
                    self.add("WARN", "Reliability", "PodDisruptionBudget", "No matching PDB was found.", "Define a PDB aligned with replica count and maintenance/eviction requirements.", namespace, ref, source=SOURCE_URLS["pdb"])
                elif matching_pdb:
                    self.add("PASS", "Reliability", "PodDisruptionBudget", f"Matched {(matching_pdb.get('metadata') or {}).get('name')}", namespace=namespace, workload=ref, source=SOURCE_URLS["pdb"])
                if hpa:
                    spec = hpa.get("spec") or {}
                    self.add("PASS", "Autoscaling", "HPA coverage", f"min={spec.get('minReplicas',1)}; max={spec.get('maxReplicas')}; metrics={len(spec.get('metrics') or [])}", namespace=namespace, workload=ref, source=SOURCE_URLS["hpa"])
                    if not spec.get("behavior"):
                        self.add("INFO", "Autoscaling", "HPA behavior", "No explicit scaleUp/scaleDown behavior.", "Review stabilization windows and scaling policies against traffic behavior.", namespace, ref, source=SOURCE_URLS["hpa"])
                elif keda:
                    spec = keda.get("spec") or {}
                    self.add("PASS", "Autoscaling", "KEDA coverage", f"min={spec.get('minReplicaCount',0)}; max={spec.get('maxReplicaCount',100)}; triggers={len(spec.get('triggers') or [])}", namespace=namespace, workload=ref, source=SOURCE_URLS["keda"])
                    if spec.get("minReplicaCount", 0) == 0 and not spec.get("fallback"):
                        self.add("WARN", "Autoscaling", "KEDA fallback", "Scale-to-zero is enabled without fallback.", "For external metrics, evaluate fallback replicas and failureThreshold for metric-backend outages.", namespace, ref, source=SOURCE_URLS["keda"])
                else:
                    self.add("INFO", "Autoscaling", "Dynamic scaling", "No HPA or KEDA ScaledObject targets this workload.", "Use demand and Prometheus history to decide whether HPA or event-driven KEDA is appropriate.", namespace, ref, source=SOURCE_URLS["hpa"])
            if not maps["network"].get(namespace) and namespace not in SYSTEM_NAMESPACES:
                self.add("WARN", "Network", "NetworkPolicy coverage", "Namespace has workloads but no NetworkPolicy object.", "Start with default-deny ingress/egress and allow only required flows.", namespace, ref, source=SOURCE_URLS["network"])
            self.workloads.append({
                "kind": kind, "namespace": namespace, "name": name, "reference": ref,
                "replicas": replicas, "readyReplicas": ready, "systemNamespace": namespace in SYSTEM_NAMESPACES,
                "containers": container_rows, "technologies": sorted(technologies),
                "autoscaling": "KEDA" if keda else "HPA" if hpa else "None",
                "pdb": (matching_pdb.get("metadata") or {}).get("name") if matching_pdb else None,
                "topologySpreadConstraints": len(podspec.get("topologySpreadConstraints") or []),
                "networkPolicyObjectsInNamespace": maps["network"].get(namespace, 0),
            })
        app_namespaces = sorted({w["namespace"] for w in self.workloads if w["namespace"] not in SYSTEM_NAMESPACES})
        for namespace in app_namespaces:
            self.add("PASS" if maps["quotas"].get(namespace) else "INFO", "Governance", "ResourceQuota", f"objects={maps['quotas'].get(namespace,0)}", "For multi-tenant/shared clusters, define quotas consistent with capacity and ownership.", namespace=namespace)
            self.add("PASS" if maps["limits"].get(namespace) else "INFO", "Governance", "LimitRange", f"objects={maps['limits'].get(namespace,0)}", "Use LimitRange defaults only as a guardrail; workload-specific values should come from telemetry.", namespace=namespace)

    def analyze_rbac_and_storage(self) -> None:
        wildcard_roles = []
        for key in ("roles", "clusterroles"):
            for role in items(self.raw.get(key)):
                for rule in role.get("rules") or []:
                    if "*" in (rule.get("verbs") or []) or "*" in (rule.get("resources") or []):
                        meta = role.get("metadata") or {}
                        wildcard_roles.append(f"{role.get('kind')}/{meta.get('namespace','-')}/{meta.get('name','-')}")
                        break
        self.add("WARN" if wildcard_roles else "PASS", "Security", "RBAC wildcards", f"{len(wildcard_roles)} role(s) with wildcard verbs/resources" + (f"; sample={', '.join(wildcard_roles[:10])}" if wildcard_roles else ""), "Replace wildcards with exact API groups, resources, resourceNames and verbs.", source=SOURCE_URLS["security"])
        cluster_admin = []
        for binding in items(self.raw.get("clusterrolebindings")):
            if ((binding.get("roleRef") or {}).get("name") == "cluster-admin"):
                cluster_admin.append((binding.get("metadata") or {}).get("name", "-"))
        self.add("WARN" if cluster_admin else "PASS", "Security", "cluster-admin bindings", f"{len(cluster_admin)} binding(s): {', '.join(cluster_admin[:10]) or 'none'}", "Review every cluster-admin subject and prefer scoped roles with time-bound elevation.", source=SOURCE_URLS["security"])
        pvcs = items(self.base["pvcs"])
        unbound = [f"{(x.get('metadata') or {}).get('namespace')}/{(x.get('metadata') or {}).get('name')}" for x in pvcs if (x.get("status") or {}).get("phase") != "Bound"]
        self.add("WARN" if unbound else "PASS", "Storage", "PVC health", f"{len(unbound)} unbound PVC(s) out of {len(pvcs)}" + (f"; {', '.join(unbound[:10])}" if unbound else ""), "Inspect StorageClass, CSI health, capacity, access modes and topology constraints.")
        ingress_without_tls = []
        for ingress in items(self.raw.get("ingresses")):
            if not (ingress.get("spec") or {}).get("tls"):
                meta = ingress.get("metadata") or {}
                ingress_without_tls.append(f"{meta.get('namespace')}/{meta.get('name')}")
        if items(self.raw.get("ingresses")):
            self.add("WARN" if ingress_without_tls else "PASS", "Security", "Ingress TLS", f"{len(ingress_without_tls)} ingress(es) without spec.tls", "Terminate TLS with managed certificates and enforce HTTPS end-to-end where required.", source=SOURCE_URLS["gateway"])
        else:
            self.add("N/A", "Security", "Ingress TLS", "No Ingress resources detected.", source=SOURCE_URLS["gateway"])

    def analyze_technologies(self) -> list[dict[str, Any]]:
        technologies: list[dict[str, Any]] = []
        for technology in TECH_PATTERNS:
            refs = sorted(self.technology_workloads.get(technology, set()))
            if not refs:
                self.add("N/A", "Technology", technology, f"{technology} was not detected in workload image/name/command/runtime options.", technology=technology)
                technologies.append({"name": technology, "state": "N/A", "workloads": []})
                continue
            technologies.append({"name": technology, "state": "DETECTED", "workloads": refs})
            self.add("INFO", "Technology", technology, f"Detected in {len(refs)} container(s).", "Review the technology-specific checks and confirm runtime/version from the image SBOM.", technology=technology)
        for row in self.workloads:
            for container in row["containers"]:
                if container["type"] != "app": continue
                namespace, ref, cname = row["namespace"], row["reference"], container["name"]
                options = container["runtimeOptions"]
                if "Java" in container["technologies"]:
                    joined = " ".join(options.values())
                    if not options:
                        self.add("WARN", "Runtime", "Java container ergonomics", "Java detected but no JAVA_OPTS/JAVA_TOOL_OPTIONS/JDK_JAVA_OPTIONS is visible.", "Confirm JDK version and use telemetry to reserve native headroom; prefer a controlled MaxRAMPercentage or validated -Xmx policy.", namespace, ref, cname, SOURCE_URLS["java"], "Java")
                    elif not re.search(r"(?i)(-Xmx|-XX:MaxRAMPercentage)", joined):
                        self.add("WARN", "Runtime", "Java heap sizing", "Runtime options do not expose -Xmx or MaxRAMPercentage.", "Set a measured heap policy (often 65-75% of the container memory limit) leaving headroom for metaspace, threads, direct buffers and agents.", namespace, ref, cname, SOURCE_URLS["java"], "Java")
                    else:
                        self.add("PASS", "Runtime", "Java heap sizing", "Explicit heap policy detected in approved runtime option variables.", namespace=namespace, workload=ref, container=cname, source=SOURCE_URLS["java"], technology="Java")
                    if "-XX:+ExitOnOutOfMemoryError" not in joined:
                        self.add("INFO", "Runtime", "Java OOM behavior", "ExitOnOutOfMemoryError was not detected.", "Evaluate fail-fast restart behavior; enable heap dumps only with bounded persistent storage and a secure collection process.", namespace, ref, cname, SOURCE_URLS["java"], "Java")
                    xmx = re.search(r"(?i)-Xmx([0-9]+(?:\.[0-9]+)?)([kmg])", joined)
                    limit = memory_bytes((container.get("limits") or {}).get("memory"))
                    if xmx and limit:
                        heap = float(xmx.group(1)) * {"k": 2**10, "m": 2**20, "g": 2**30}[xmx.group(2).lower()]
                        if heap > limit * .80:
                            self.add("CRIT", "Runtime", "Java native-memory headroom", f"-Xmx is {heap/limit:.0%} of memory limit.", "Reduce heap or increase the measured memory limit; leave room for metaspace, thread stacks, direct memory and agents.", namespace, ref, cname, SOURCE_URLS["java"], "Java")
                if ".NET" in container["technologies"]:
                    if not any(name in options for name in ("DOTNET_GCHeapHardLimit", "DOTNET_GCHeapHardLimitPercent", "COMPlus_GCHeapHardLimit", "COMPlus_GCHeapHardLimitPercent")):
                        self.add("INFO", "Runtime", ".NET GC/container memory", "No explicit GC heap hard-limit setting is visible.", "Confirm .NET runtime version and use memory p95/p99 to decide whether GCHeapHardLimitPercent is needed while preserving native-memory headroom.", namespace, ref, cname, SOURCE_URLS["dotnet"], ".NET")
                    if options.get("DOTNET_EnableDiagnostics") in {"1", "true", "True"}:
                        self.add("INFO", "Runtime", ".NET diagnostics", "Runtime diagnostics are enabled.", "Restrict diagnostic socket access and disable it when production diagnostics are not required.", namespace, ref, cname, SOURCE_URLS["dotnet"], ".NET")
                if "Kafka" in container["technologies"]:
                    if row["replicas"] and row["replicas"] < 3:
                        self.add("WARN", "Runtime", "Kafka broker redundancy", f"replicas={row['replicas']}", "For production, validate at least three brokers, replication factor, min.insync.replicas, rack/zone awareness, PDB and durable volumes.", namespace, ref, cname, SOURCE_URLS["kafka"], "Kafka")
                    if not row["pdb"] or not row["topologySpreadConstraints"]:
                        self.add("WARN", "Runtime", "Kafka disruption/topology", f"PDB={row['pdb'] or 'none'}; topologySpread={row['topologySpreadConstraints']}", "Protect quorum with PDB and zone-aware scheduling; validate one-broker-at-a-time maintenance.", namespace, ref, cname, SOURCE_URLS["kafka"], "Kafka")
                if "RabbitMQ" in container["technologies"]:
                    if row["kind"] != "StatefulSet" or row["replicas"] < 3:
                        self.add("WARN", "Runtime", "RabbitMQ quorum topology", f"kind={row['kind']}; replicas={row['replicas']}", "For quorum queues, validate an odd cluster of at least three persistent nodes distributed across failure domains.", namespace, ref, cname, SOURCE_URLS["rabbitmq"], "RabbitMQ")
                    if "RABBITMQ_VM_MEMORY_HIGH_WATERMARK" not in options and "RABBITMQ_SERVER_ADDITIONAL_ERL_ARGS" not in options:
                        self.add("INFO", "Runtime", "RabbitMQ memory watermark", "No explicit memory watermark is visible in approved runtime variables.", "Verify vm_memory_high_watermark against the container memory limit and alert before memory/disk alarms block publishers.", namespace, ref, cname, SOURCE_URLS["rabbitmq"], "RabbitMQ")
                if "Nginx" in container["technologies"]:
                    if row["autoscaling"] == "None":
                        self.add("INFO", "Runtime", "Nginx scaling", "No HPA/KEDA targets this Nginx workload.", "Use request rate, active connections, latency and CPU history to size replicas; validate worker_processes auto and connection limits in configuration.", namespace, ref, cname, SOURCE_URLS["nginx"], "Nginx")
                if "API Gateway" in container["technologies"]:
                    if row["replicas"] < 2 and row["autoscaling"] == "None":
                        self.add("WARN", "Runtime", "Gateway availability", f"replicas={row['replicas']}; autoscaling={row['autoscaling']}", "Run redundant gateway replicas across failure domains and validate HPA, PDB, TLS, timeouts, retries and rate limits.", namespace, ref, cname, SOURCE_URLS["gateway"], "API Gateway")
        return technologies

    def prometheus_capacity(self) -> dict[str, Any]:
        telemetry = load_json(self.directory / "prometheus-telemetry.json", {"state": "DISABLED", "workloads": []})
        state = telemetry.get("state", "DISABLED")
        if state in {"DISABLED", "UNAVAILABLE"}:
            self.add("N/A", "Observability", "Prometheus capacity analysis", f"state={state}; reason={telemetry.get('reason','not provided')}", "Provide an explicit HTTP/HTTPS Prometheus URL to calculate p50/p90/p95/p99; absence of metrics is not compliance.")
            return telemetry
        lookup = {(w["namespace"], w["name"]): w for w in self.workloads if w["kind"] == "Deployment"}
        for measured in telemetry.get("workloads") or []:
            namespace, name = measured.get("namespace"), measured.get("deployment")
            row = lookup.get((namespace, name))
            metrics = measured.get("metrics") or {}
            cpu = metrics.get("cpu") or {}
            usage_memory = metrics.get("memory") or {}
            working_set = metrics.get("memory_working_set") or {}
            memory = working_set if working_set.get("state") == "AVAILABLE" else usage_memory
            if not row or cpu.get("state") != "AVAILABLE" or memory.get("state") != "AVAILABLE":
                continue
            replicas = max(1, int(row.get("readyReplicas") or row.get("replicas") or 1))
            cpu_request = max(.001, float(cpu.get("p90") or 0) / replicas * 1.15)
            cpu_limit = max(cpu_request, float(cpu.get("p99") or cpu.get("peak") or 0) / replicas * 1.25)
            mem_request = max(2**20, float(memory.get("p90") or 0) / replicas * 1.10)
            mem_limit = max(mem_request, float(memory.get("p99") or memory.get("peak") or 0) / replicas * 1.20)

            def current_total(section: str, resource: str, parser) -> float | None:
                values: list[float] = []
                for container in row.get("containers") or []:
                    if container.get("type") != "app":
                        continue
                    parsed = parser((container.get(section) or {}).get(resource))
                    if parsed is None:
                        return None
                    values.append(parsed)
                return sum(values) if values else None

            current_cpu_request = current_total("requests", "cpu", cpu_cores)
            current_cpu_limit = current_total("limits", "cpu", cpu_cores)
            current_mem_request = current_total("requests", "memory", memory_bytes)
            current_mem_limit = current_total("limits", "memory", memory_bytes)
            samples = min(int(cpu.get("samples") or 0), int(memory.get("samples") or 0))
            confidence = "HISTORICAL" if samples >= 100 else "LOW_SAMPLE"
            issues: list[str] = []
            if current_cpu_request is None: issues.append("CPU request ausente/misto")
            elif current_cpu_request < cpu_request * .70: issues.append("CPU request abaixo da faixa histórica proposta")
            elif current_cpu_request > cpu_request * 2.5: issues.append("CPU request potencialmente superdimensionado")
            if current_mem_request is None: issues.append("memory request ausente/misto")
            elif current_mem_request < mem_request * .70: issues.append("memory request abaixo da faixa histórica proposta")
            elif current_mem_request > mem_request * 2.5: issues.append("memory request potencialmente superdimensionado")
            if current_mem_limit is None: issues.append("memory limit ausente/misto")
            elif current_mem_limit < mem_limit: issues.append("memory limit abaixo de p99 + headroom proposto")
            if current_cpu_limit is None: issues.append("CPU limit ausente/misto; revisar política de throttling")

            cpu_variability = float(cpu.get("p99") or 0) / max(float(cpu.get("p50") or 0), .001)
            memory_variability = float(memory.get("p99") or 0) / max(float(memory.get("p50") or 0), 2**20)
            technology_text = " ".join(row.get("technologies") or [])
            identity = f"{name} {technology_text}".lower()
            if row.get("autoscaling") != "None":
                scaling = f"Recalibrar {row.get('autoscaling')} existente com os percentis e a métrica de negócio."
            elif re.search(r"(worker|consumer|processor|queue|kafka|rabbit)", identity):
                scaling = "Candidato a KEDA somente se backlog/lag/queue depth representar demanda; definir fallback e comportamento do HPA."
            elif cpu_variability >= 2.0 or memory_variability >= 1.5:
                scaling = "Candidato a HPA após teste de carga; escolher métrica, target e stabilization windows com requests corretos."
            else:
                scaling = "Sem evidência forte para autoscaling nesta janela; manter observação e validar sazonalidade/carga."
            recommendation = {
                "namespace": namespace, "workload": f"Deployment/{name}", "window": telemetry.get("window"),
                "replicasObserved": replicas, "confidence": confidence, "samples": samples,
                "current": {
                    "cpuRequestPerReplica": fmt_cpu(current_cpu_request) if current_cpu_request is not None else "unset/mixed",
                    "cpuLimitPerReplica": fmt_cpu(current_cpu_limit) if current_cpu_limit is not None else "unset/mixed",
                    "memoryRequestPerReplica": fmt_memory(current_mem_request) if current_mem_request is not None else "unset/mixed",
                    "memoryLimitPerReplica": fmt_memory(current_mem_limit) if current_mem_limit is not None else "unset/mixed",
                },
                "cpu": {"requestPerReplica": fmt_cpu(cpu_request), "limitPerReplica": fmt_cpu(cpu_limit), "p90AggregateCores": cpu.get("p90"), "p99AggregateCores": cpu.get("p99")},
                "memory": {"sourceMetric": memory.get("metric"), "requestPerReplica": fmt_memory(mem_request), "limitPerReplica": fmt_memory(mem_limit), "p90AggregateBytes": memory.get("p90"), "p99AggregateBytes": memory.get("p99")},
                "assessment": issues or ["current values are within the broad statistical band"],
                "scalingRecommendation": scaling,
                "caveat": "Validate container attribution, startup peaks, seasonality, JVM/native/cache headroom and CPU throttling before changing manifests.",
            }
            self.capacity.append(recommendation)
            severity = "WARN" if confidence == "HISTORICAL" and issues else "INFO"
            action = "; ".join(issues) if issues else "sem desvio amplo detectado"
            self.add(severity, "Capacity", "Prometheus sizing proposal", f"Atual CPU {recommendation['current']['cpuRequestPerReplica']}/{recommendation['current']['cpuLimitPerReplica']} -> proposta {recommendation['cpu']['requestPerReplica']}/{recommendation['cpu']['limitPerReplica']}; memória {recommendation['current']['memoryRequestPerReplica']}/{recommendation['current']['memoryLimitPerReplica']} -> {recommendation['memory']['requestPerReplica']}/{recommendation['memory']['limitPerReplica']}; {action}. {scaling}", recommendation["caveat"], namespace, f"Deployment/{name}", source=SOURCE_URLS["resources"])
        if self.capacity:
            self.add("PASS", "Observability", "Prometheus capacity analysis", f"Generated {len(self.capacity)} workload recommendation(s) from available time series.", source=SOURCE_URLS["resources"])
        else:
            self.add("N/A", "Observability", "Prometheus capacity analysis", f"Collector state={state}, but no Deployment had both CPU and memory series.", "Verify labels, scrape coverage and retention; do not infer compliance from missing series.")
        return telemetry

    def extension_coverage(self) -> None:
        resources = self.collection.get("resources") or {}
        for label, key in (("KEDA", "keda_scaledobjects"), ("VPA", "vpas"), ("Gateway API", "gateways"), ("ServiceMonitor", "servicemonitors"), ("Karpenter", "karpenter_nodepools"), ("Istio", "istio_virtualservices"), ("Kyverno", "kyverno_clusterpolicies")):
            entry = resources.get(key, {})
            if entry.get("state") == "N/A":
                self.add("N/A", "Extensions", label, "API resource is not served by this cluster.")
            elif entry.get("state") == "UNAVAILABLE":
                self.add("WARN", "Extensions", label, f"API exists but collection failed: {entry.get('reason','unknown')}", "Validate RBAC and API service health.")
            else:
                self.add("PASS" if entry.get("count", 0) else "INFO", "Extensions", label, f"API available; objects={entry.get('count',0)}", "Review controller health and object status." if entry.get("count", 0) else "API is installed but no objects were found.")

    def manifests(self) -> None:
        application_keys = ["jobs", "cronjobs", "services", "ingresses", "hpas", "vpas", "keda_scaledobjects", "pdbs", "networkpolicies", "serviceaccounts", "gateways", "httproutes", "rollouts"]
        evidence = [sanitize_tree(x) for x in items(self.base["workloads"])]
        for key in application_keys:
            evidence.extend(sanitize_tree(x) for x in items(self.raw.get(key)))
        output = {
            "schemaVersion": SCHEMA_VERSION, "generatedAt": utcnow(),
            "notice": "Sanitized application evidence. Secret data, ConfigMap values, arbitrary env values, status and managed fields are excluded.",
            "items": evidence,
        }
        (self.directory / "application-manifests-sanitized.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    def persist_sanitized_snapshots(self) -> None:
        """Remove literal environment/config values from the base evidence on disk."""
        for filename, value in (
            ("nodes.json", self.base["nodes"]),
            ("pods.json", self.base["pods"]),
            ("workloads.json", self.base["workloads"]),
            ("namespaces.json", self.base["namespaces"]),
            ("pvcs.json", self.base["pvcs"]),
        ):
            path = self.directory / filename
            if path.exists():
                path.write_text(json.dumps(sanitize_snapshot_tree(value), ensure_ascii=False, indent=2), encoding="utf-8")
                self.sanitized_snapshots.append(filename)
        events_path = self.directory / "events.json"
        if events_path.exists():
            events_path.write_text(json.dumps(sanitize_events(self.base["events"]), ensure_ascii=False, indent=2), encoding="utf-8")
            self.sanitized_snapshots.append("events.json")

    def result(self) -> dict[str, Any]:
        self.cluster_health()
        self.analyze_workloads()
        self.analyze_rbac_and_storage()
        self.semantic_summary = apply_semantic_assessment(self)
        self.integrate_aws_eks()
        technologies = self.analyze_technologies()
        telemetry = self.prometheus_capacity()
        self.extension_coverage()
        self.manifests()
        self.persist_sanitized_snapshots()
        cloud_provider = generate_cloud_provider_assessment(self.directory)
        cis_security = generate_cis_security(self.directory, self.raw, self.base, self.collection, self.aws_eks)
        operational = generate_operational_insights(
            self.directory, self.workloads, self.findings, self.capacity, technologies, self.aws_eks, cloud_provider
        )
        order = {"CRIT": 0, "WARN": 1, "UNKNOWN": 2, "PARTIAL": 3, "INFO": 4, "PASS": 5, "N/A": 6}
        self.findings.sort(key=lambda x: (order.get(x["severity"], 9), x["category"], x["namespace"], x["workload"], x["check"]))
        counts = Counter(x["severity"] for x in self.findings)
        categories: list[dict[str, Any]] = []
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for finding in self.findings: grouped[finding["category"]].append(finding)
        for category, values in sorted(grouped.items()):
            severities = {x["severity"] for x in values}
            state = next((level for level in ("CRIT", "WARN", "UNKNOWN", "PARTIAL", "INFO", "PASS", "N/A") if level in severities), "N/A")
            categories.append({"name": category, "state": state, "counts": dict(Counter(x["severity"] for x in values))})
        collection_bytes = sum(path.stat().st_size for path in self.directory.rglob("*") if path.is_file())
        quality = finding_quality(self.findings)
        return {
            "schemaVersion": SCHEMA_VERSION, "generatedAt": utcnow(), "readOnly": True,
            "safety": {"kubectlVerbs": ["get", "list"], "secrets": "not-collected", "configMapValues": "not-collected", "environmentValues": "redacted-except-runtime-tuning", "eventMessages": "omitted", "mutations": 0},
            "summary": {
                "checks": len(self.findings), "critical": counts["CRIT"], "warnings": counts["WARN"],
                "unknown": counts["UNKNOWN"], "partial": counts["PARTIAL"],
                "info": counts["INFO"], "passed": counts["PASS"], "notApplicable": counts["N/A"],
                "workloads": len(self.workloads), "containers": sum(len(x["containers"]) for x in self.workloads),
                "technologiesDetected": sum(x["state"] == "DETECTED" for x in technologies),
                "capacityRecommendations": len(self.capacity),
                "apiResourceTypes": (self.collection.get("universalInventory") or {}).get("resourceTypes", 0),
                "objectsInventoried": (self.collection.get("universalInventory") or {}).get("objectCount", 0),
            },
            "collection": self.collection, "categories": categories, "findings": self.findings,
            "quality": quality,
            "performance": {"requestBudget": self.collection.get("requestBudget") or {}, "processPeakRssBytes": peak_rss_bytes(), "collectionBytesBeforeReport": collection_bytes},
            "workloads": self.workloads, "technologies": technologies,
            "capacityRecommendations": self.capacity,
            "semantic": self.semantic_summary,
            "cisSecurity": cis_security,
            "operationalInsights": operational,
            "cloudProvider": cloud_provider,
            "awsEks": {
                "state": self.aws_eks.get("state", "UNKNOWN") if isinstance(self.aws_eks, dict) else "UNKNOWN",
                "reason": self.aws_eks.get("reason", "") if isinstance(self.aws_eks, dict) else "",
                "summary": self.aws_eks.get("summary", {}) if isinstance(self.aws_eks, dict) else {},
                "coverage": self.aws_eks.get("coverage", {}) if isinstance(self.aws_eks, dict) else {},
                "inventory": self.aws_eks.get("inventory", {}) if isinstance(self.aws_eks, dict) else {},
            },
            "prometheus": {"state": telemetry.get("state", "DISABLED"), "window": telemetry.get("window"), "reason": telemetry.get("reason", "")},
            "artifacts": {"sanitizedManifests": "application-manifests-sanitized.json", "sanitizedSnapshots": self.sanitized_snapshots, "apiResources": "api-resources.json", "universalInventory": "universal-inventory.json", "awsEks": "aws-eks-assessment.json", "cloudProvider": "cloud-provider-assessment.json", "cisSecurity": "cis-security-assessment.json", "operationalInsights": "operational-insights.json"},
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Comprehensive adaptive read-only EKS/Kubernetes assessment")
    parser.add_argument("--snapshot-dir", required=True, type=Path)
    parser.add_argument("--collect-live", action="store_true", help="collect additional resources with read-only kubectl GET")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--chunk-size", type=int, default=200)
    parser.add_argument("--inventory-workers", type=int, default=4)
    parser.add_argument("--api-delay-ms", type=int, default=int(os.getenv("ASSESSMENT_API_DELAY_MS", "100")))
    parser.add_argument("--retries", type=int, default=int(os.getenv("ASSESSMENT_API_RETRIES", "3")))
    parser.add_argument("--max-requests", type=int, default=int(os.getenv("ASSESSMENT_MAX_REQUESTS", "1000")))
    parser.add_argument("--max-duration", type=int, default=int(os.getenv("ASSESSMENT_MAX_DURATION", "1800")))
    parser.add_argument("--max-response-mb", type=int, default=int(os.getenv("ASSESSMENT_MAX_RESPONSE_MB", "256")))
    parser.add_argument("--namespace", default=os.getenv("ASSESSMENT_NAMESPACE", ""), help="optional namespaced collection scope")
    parser.add_argument("--resume", action="store_true", help="reuse valid resource snapshots already present")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 5 <= args.timeout <= 300: parser.error("--timeout must be between 5 and 300")
    if not 50 <= args.chunk_size <= 1000: parser.error("--chunk-size must be between 50 and 1000")
    if not 1 <= args.inventory_workers <= 16: parser.error("--inventory-workers must be between 1 and 16")
    if not 0 <= args.api_delay_ms <= 5000: parser.error("--api-delay-ms must be between 0 and 5000")
    if not 0 <= args.retries <= 8: parser.error("--retries must be between 0 and 8")
    if not 10 <= args.max_requests <= 100000: parser.error("--max-requests must be between 10 and 100000")
    if not 60 <= args.max_duration <= 86400: parser.error("--max-duration must be between 60 and 86400")
    if not 16 <= args.max_response_mb <= 4096: parser.error("--max-response-mb must be between 16 and 4096")
    if args.namespace and not re.fullmatch(r"[a-z0-9]([-a-z0-9.]*[a-z0-9])?", args.namespace): parser.error("invalid --namespace")
    return args


def main() -> int:
    args = parse_args()
    directory = args.snapshot_dir.resolve()
    if not directory.is_dir():
        print(f"snapshot directory not found: {directory}", file=sys.stderr)
        return 2
    budget = ApiBudget(args.max_requests, args.max_duration, args.max_response_mb * 1024 * 1024, args.api_delay_ms)
    if args.resume:
        valid, reason = valid_resume_provenance(directory, args.namespace)
        if not valid:
            print(f"unsafe resume refused: {reason}", file=sys.stderr)
            return 2
    raw, collection = (
        collect_live(directory, args.timeout, args.chunk_size, args.inventory_workers,
                     budget, args.retries, args.namespace, args.resume)
        if args.collect_live else existing_resources(directory)
    )
    result = Assessment(directory, raw, collection).result()
    output = args.output.resolve() if args.output else directory / "comprehensive-assessment.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.collect_live:
        write_collection_provenance(directory, args.namespace)
    summary = result["summary"]
    print(json.dumps({"output": str(output), **summary}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
