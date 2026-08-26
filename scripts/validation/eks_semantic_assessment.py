#!/usr/bin/env python3
"""Semantic, read-only Kubernetes rules for the adaptive EKS assessment."""
from __future__ import annotations

import datetime as dt
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

SOURCES = {
    "nodes": "https://kubernetes.io/docs/concepts/architecture/nodes/",
    "security": "https://kubernetes.io/docs/concepts/security/pod-security-standards/",
    "rbac": "https://kubernetes.io/docs/concepts/security/rbac-good-practices/",
    "network": "https://kubernetes.io/docs/concepts/services-networking/network-policies/",
    "workloads": "https://kubernetes.io/docs/concepts/workloads/",
    "probes": "https://kubernetes.io/docs/concepts/configuration/liveness-readiness-startup-probes/",
    "autoscaling": "https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/",
    "storage": "https://kubernetes.io/docs/concepts/storage/",
    "upgrade": "https://docs.aws.amazon.com/eks/latest/userguide/update-cluster.html",
    "supply": "https://docs.aws.amazon.com/eks/latest/best-practices/image-security.html",
    "dr": "https://kubernetes.io/docs/concepts/storage/volume-snapshots/",
}
DANGEROUS_CAPABILITIES = {
    "SYS_ADMIN",
    "SYS_MODULE",
    "SYS_PTRACE",
    "NET_ADMIN",
    "DAC_READ_SEARCH",
    "DAC_OVERRIDE",
    "SYS_RAWIO",
    "SYS_BOOT",
    "SYS_TIME",
}
SAFE_SYSCTLS = (
    "kernel.shm_rmid_forced",
    "net.ipv4.ip_local_port_range",
    "net.ipv4.tcp_syncookies",
    "net.ipv4.ping_group_range",
)
DEPRECATED_APIS = {
    "extensions/v1beta1",
    "apps/v1beta1",
    "apps/v1beta2",
    "networking.k8s.io/v1beta1",
    "policy/v1beta1",
    "batch/v1beta1",
    "autoscaling/v2beta1",
    "autoscaling/v2beta2",
    "apiextensions.k8s.io/v1beta1",
    "admissionregistration.k8s.io/v1beta1",
    "rbac.authorization.k8s.io/v1beta1",
    "storage.k8s.io/v1beta1",
}


def items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict) and isinstance(value.get("items"), list):
        return [item for item in value["items"] if isinstance(item, dict)]
    return []


def metadata(value: dict[str, Any]) -> dict[str, Any]:
    return value.get("metadata") or {}


def object_ref(value: dict[str, Any]) -> tuple[str, str, str]:
    meta = metadata(value)
    return (
        str(meta.get("namespace") or "default"),
        str(value.get("kind") or "Object"),
        str(meta.get("name") or "-"),
    )


def selector_matches(selector: dict[str, Any], labels: dict[str, str]) -> bool:
    if selector is None:
        return False
    for key, expected in (selector.get("matchLabels") or {}).items():
        if labels.get(key) != expected:
            return False
    for expression in selector.get("matchExpressions") or []:
        key = str(expression.get("key") or "")
        operator = expression.get("operator")
        values = expression.get("values") or []
        present = key in labels
        if operator == "In" and (not present or labels.get(key) not in values):
            return False
        if operator == "NotIn" and present and labels.get(key) in values:
            return False
        if operator == "Exists" and not present:
            return False
        if operator == "DoesNotExist" and present:
            return False
    return True


def pod_template(value: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    spec = value.get("spec") or {}
    kind = value.get("kind")
    if kind == "CronJob":
        template = nested(spec, "jobTemplate", "spec", "template", fallback={})
    elif kind in {"Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job", "Rollout"}:
        template = spec.get("template") or {}
    elif kind == "Pod":
        return metadata(value), spec
    else:
        template = spec.get("template") or {}
    return template.get("metadata") or {}, template.get("spec") or {}


def nested(value: dict[str, Any], *keys: str, fallback: Any = None) -> Any:
    current: Any = value
    for key in keys:
        if not isinstance(current, dict):
            return fallback
        current = current.get(key)
    return fallback if current is None else current


def cpu_cores(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    try:
        if text.endswith("n"):
            return float(text[:-1]) / 1_000_000_000
        if text.endswith("u"):
            return float(text[:-1]) / 1_000_000
        if text.endswith("m"):
            return float(text[:-1]) / 1000
        return float(text)
    except ValueError:
        return None


def memory_bytes(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    factors = {
        "Ki": 2**10,
        "Mi": 2**20,
        "Gi": 2**30,
        "Ti": 2**40,
        "K": 10**3,
        "M": 10**6,
        "G": 10**9,
        "T": 10**12,
    }
    try:
        for suffix, factor in factors.items():
            if text.endswith(suffix):
                return float(text[: -len(suffix)]) * factor
        return float(text)
    except ValueError:
        return None


def pod_request(spec: dict[str, Any], resource: str) -> float:
    parser = cpu_cores if resource == "cpu" else memory_bytes
    regular = sum(
        parser(nested(container, "resources", "requests", resource)) or 0.0
        for container in spec.get("containers") or []
    )
    init = max(
        [
            parser(nested(container, "resources", "requests", resource)) or 0.0
            for container in spec.get("initContainers") or []
        ]
        or [0.0]
    )
    overhead = parser(nested(spec, "overhead", resource)) or 0.0
    return max(regular, init) + overhead


def condition(value: dict[str, Any], kind: str) -> tuple[str, str]:
    for item in nested(value, "status", "conditions", fallback=[]) or []:
        if item.get("type") == kind:
            return str(item.get("status") or "Unknown"), str(item.get("reason") or item.get("message") or "")
    return "Unknown", "condition absent"


def parse_time(value: Any) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def percent_or_int(value: Any, total: int) -> int | None:
    if value is None:
        return None
    text = str(value)
    try:
        if text.endswith("%"):
            return math.ceil(total * float(text[:-1]) / 100)
        return int(text)
    except ValueError:
        return None


def all_workload_items(assessment: Any) -> list[dict[str, Any]]:
    return [item for item in assessment.workload_objects() if item.get("kind") != "ReplicaSet"]


class SemanticRules:
    def __init__(self, assessment: Any):
        self.a = assessment
        self.raw = assessment.raw
        self.base = assessment.base
        self.workloads = all_workload_items(assessment)
        self.row_map = {
            (row.get("namespace"), row.get("kind"), row.get("name")): row
            for row in assessment.workloads
        }
        self.stats: Counter[str] = Counter()

    def add(
        self,
        severity: str,
        rule_id: str,
        category: str,
        check: str,
        detail: str,
        recommendation: str = "",
        namespace: str = "-",
        workload: str = "-",
        container: str = "-",
        source: str = "",
        confidence: str = "HIGH",
    ) -> None:
        self.stats[severity] += 1
        self.a.add(
            severity,
            category,
            check,
            detail,
            recommendation,
            namespace,
            workload,
            container,
            source=source,
            evidence="semantic-snapshot",
            rule_id=rule_id,
            confidence=confidence,
        )

    def coverage_state(self, key: str) -> str:
        return str(
            nested(
                self.a.collection,
                "resources",
                key,
                "state",
                fallback="UNKNOWN",
            )
        )

    def nodes(self) -> None:
        nodes = items(self.base.get("nodes"))
        pods = items(self.base.get("pods"))
        if not nodes:
            self.add(
                "UNKNOWN",
                "k8s.nodes.inventory",
                "Nodes",
                "Node semantic inventory",
                "Node snapshot is unavailable or empty.",
                "Restore read access to nodes before judging data-plane health.",
                source=SOURCES["nodes"],
            )
            return
        pods_by_node: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for pod in pods:
            if nested(pod, "status", "phase") not in {"Succeeded", "Failed"}:
                pods_by_node[str(nested(pod, "spec", "nodeName", fallback=""))].append(pod)
        minors: set[int] = set()
        for node in nodes:
            meta = metadata(node)
            name = str(meta.get("name") or "-")
            ref = f"Node/{name}"
            spec = node.get("spec") or {}
            status = node.get("status") or {}
            bad_pressure = [
                item.get("type")
                for item in status.get("conditions") or []
                if item.get("type") in {"MemoryPressure", "DiskPressure", "PIDPressure", "NetworkUnavailable"}
                and item.get("status") == "True"
            ]
            self.add(
                "CRIT" if bad_pressure else "PASS",
                "k8s.nodes.pressure",
                "Nodes",
                "Node pressure conditions",
                f"active={','.join(map(str, bad_pressure)) or 'none'}",
                "Resolve disk, memory, PID, CNI or kubelet pressure before scheduling more workloads.",
                workload=ref,
                source=SOURCES["nodes"],
            )
            if spec.get("unschedulable"):
                self.add(
                    "INFO",
                    "k8s.nodes.unschedulable",
                    "Nodes",
                    "Unschedulable node",
                    "spec.unschedulable=true",
                    "Confirm that cordon is intentional and not reducing required capacity.",
                    workload=ref,
                    source=SOURCES["nodes"],
                )
            version = str(nested(status, "nodeInfo", "kubeletVersion", fallback=""))
            match = re.search(r"v?1\.(\d+)", version)
            if match:
                minors.add(int(match.group(1)))
            alloc_cpu = cpu_cores(nested(status, "allocatable", "cpu"))
            alloc_mem = memory_bytes(nested(status, "allocatable", "memory"))
            requested_cpu = sum(pod_request(pod.get("spec") or {}, "cpu") for pod in pods_by_node.get(name, []))
            requested_mem = sum(pod_request(pod.get("spec") or {}, "memory") for pod in pods_by_node.get(name, []))
            cpu_ratio = 100 * requested_cpu / alloc_cpu if alloc_cpu else None
            mem_ratio = 100 * requested_mem / alloc_mem if alloc_mem else None
            if cpu_ratio is None or mem_ratio is None:
                severity = "UNKNOWN"
            elif max(cpu_ratio, mem_ratio) >= 100:
                severity = "CRIT"
            elif max(cpu_ratio, mem_ratio) >= 90:
                severity = "WARN"
            else:
                severity = "PASS"
            self.add(
                severity,
                "k8s.nodes.request-saturation",
                "Capacity",
                "Node requested capacity",
                f"pods={len(pods_by_node.get(name, []))}; cpuRequests={cpu_ratio:.1f}%"
                if cpu_ratio is not None and mem_ratio is None
                else f"pods={len(pods_by_node.get(name, []))}; cpuRequests={cpu_ratio:.1f}%; memoryRequests={mem_ratio:.1f}%"
                if cpu_ratio is not None and mem_ratio is not None
                else "allocatable or requests unavailable",
                "Keep scheduling headroom for disruption, system daemons and traffic spikes.",
                workload=ref,
                source=SOURCES["nodes"],
            )
        self.add(
            "WARN" if len(minors) > 1 else "PASS",
            "k8s.nodes.kubelet-version-skew",
            "Upgrade",
            "Kubelet version consistency",
            f"kubeletMinorVersions={','.join(map(str, sorted(minors))) or 'unknown'}",
            "Keep kubelets within supported skew and align node pools during upgrades.",
            source=SOURCES["upgrade"],
        )

    def namespace_security(self) -> None:
        namespaces = {metadata(item).get("name"): item for item in items(self.base.get("namespaces"))}
        app_namespaces = sorted(
            {
                str(row.get("namespace"))
                for row in self.a.workloads
                if not row.get("systemNamespace")
            }
        )
        alternative = bool(items(self.raw.get("kyverno_clusterpolicies"))) or any(
            token in str(metadata(webhook).get("name", "")).lower()
            for webhook in items(self.raw.get("validatingwebhooks"))
            for token in ("gatekeeper", "kyverno")
        )
        for namespace in app_namespaces:
            labels = metadata(namespaces.get(namespace, {})).get("labels") or {}
            enforce = str(labels.get("pod-security.kubernetes.io/enforce") or "")
            version = str(labels.get("pod-security.kubernetes.io/enforce-version") or "")
            if enforce == "restricted":
                severity = "PASS"
            elif enforce == "baseline":
                severity = "INFO"
            elif enforce == "privileged":
                severity = "WARN"
            elif alternative:
                severity = "PARTIAL"
            else:
                severity = "WARN"
            self.add(
                severity,
                "k8s.security.pod-security-admission",
                "Security",
                "Pod Security enforcement",
                f"enforce={enforce or 'absent'}; version={version or 'absent'}; alternativePolicyEngine={alternative}",
                "Enforce a version-pinned Pod Security Standard or an equivalent tested admission policy.",
                namespace=namespace,
                source=SOURCES["security"],
            )

    def extended_container_security(self) -> None:
        for item in self.workloads:
            namespace, kind, name = object_ref(item)
            ref = f"{kind}/{name}"
            _, spec = pod_template(item)
            sysctls = [
                str(entry.get("name") or "")
                for entry in nested(spec, "securityContext", "sysctls", fallback=[]) or []
            ]
            unsafe = [
                name
                for name in sysctls
                if not any(name == allowed or name.startswith(allowed + ".") for allowed in SAFE_SYSCTLS)
            ]
            if unsafe:
                self.add(
                    "CRIT",
                    "k8s.security.unsafe-sysctls",
                    "Security",
                    "Unsafe sysctls",
                    f"sysctls={','.join(unsafe)}",
                    "Remove unsafe sysctls or isolate them behind a reviewed node/runtime policy.",
                    namespace,
                    ref,
                    source=SOURCES["security"],
                )
            for container in [
                *(spec.get("containers") or []),
                *(spec.get("initContainers") or []),
                *(spec.get("ephemeralContainers") or []),
            ]:
                cname = str(container.get("name") or "-")
                security = container.get("securityContext") or {}
                added = {
                    str(value).upper()
                    for value in nested(security, "capabilities", "add", fallback=[]) or []
                }
                dangerous = sorted(added & DANGEROUS_CAPABILITIES)
                if dangerous:
                    self.add(
                        "CRIT",
                        "k8s.security.dangerous-capabilities",
                        "Security",
                        "Dangerous Linux capabilities",
                        f"added={','.join(dangerous)}",
                        "Remove high-risk capabilities or document a narrowly scoped infrastructure exception.",
                        namespace,
                        ref,
                        cname,
                        SOURCES["security"],
                    )
                host_ports = [
                    int(port.get("hostPort") or 0)
                    for port in container.get("ports") or []
                    if int(port.get("hostPort") or 0) > 0
                ]
                if host_ports:
                    self.add(
                        "WARN",
                        "k8s.security.host-port",
                        "Security",
                        "Host port exposure",
                        f"hostPorts={','.join(map(str, host_ports))}",
                        "Avoid hostPort unless required; validate collisions, node exposure and NetworkPolicy behavior.",
                        namespace,
                        ref,
                        cname,
                        SOURCES["security"],
                    )
                if security.get("procMount") == "Unmasked":
                    self.add(
                        "CRIT",
                        "k8s.security.unmasked-proc",
                        "Security",
                        "Unmasked proc mount",
                        "procMount=Unmasked",
                        "Use the default masked proc mount.",
                        namespace,
                        ref,
                        cname,
                        SOURCES["security"],
                    )
                if nested(security, "seccompProfile", "type") == "Unconfined":
                    self.add(
                        "CRIT",
                        "k8s.security.seccomp-unconfined",
                        "Security",
                        "Unconfined seccomp",
                        "container seccompProfile.type=Unconfined",
                        "Use RuntimeDefault or an approved Localhost profile.",
                        namespace,
                        ref,
                        cname,
                        SOURCES["security"],
                    )
                if nested(security, "windowsOptions", "hostProcess") is True:
                    self.add(
                        "CRIT",
                        "k8s.security.windows-hostprocess",
                        "Security",
                        "Windows HostProcess",
                        "windowsOptions.hostProcess=true",
                        "Restrict HostProcess workloads to reviewed infrastructure components.",
                        namespace,
                        ref,
                        cname,
                        SOURCES["security"],
                    )

    def network_and_endpoints(self) -> None:
        policies_by_ns: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for policy in items(self.raw.get("networkpolicies")):
            policies_by_ns[str(metadata(policy).get("namespace") or "default")].append(policy)
        for item in self.workloads:
            namespace, kind, name = object_ref(item)
            ref = f"{kind}/{name}"
            template_meta, _ = pod_template(item)
            labels = template_meta.get("labels") or {}
            selected: list[dict[str, Any]] = []
            ingress = False
            egress = False
            for policy in policies_by_ns.get(namespace, []):
                spec = policy.get("spec") or {}
                if selector_matches(spec.get("podSelector") or {}, labels):
                    selected.append(policy)
                    types = set(spec.get("policyTypes") or [])
                    ingress |= "Ingress" in types or "ingress" in spec
                    egress |= "Egress" in types or "egress" in spec
            self.add(
                "PASS" if ingress and egress else "WARN" if selected else "WARN",
                "k8s.network.workload-isolation",
                "Network",
                "Workload NetworkPolicy isolation",
                f"selectedPolicies={len(selected)}; ingressIsolated={ingress}; egressIsolated={egress}",
                "Select every application pod and explicitly control both ingress and egress.",
                namespace,
                ref,
                source=SOURCES["network"],
            )
        for namespace, policies in policies_by_ns.items():
            default_ingress = False
            default_egress = False
            broad = 0
            for policy in policies:
                spec = policy.get("spec") or {}
                empty = not (spec.get("podSelector") or {})
                types = set(spec.get("policyTypes") or [])
                default_ingress |= empty and ("Ingress" in types) and not (spec.get("ingress") or [])
                default_egress |= empty and ("Egress" in types) and not (spec.get("egress") or [])
                for direction in ("ingress", "egress"):
                    for rule in spec.get(direction) or []:
                        for peer in [*(rule.get("from") or []), *(rule.get("to") or [])]:
                            if nested(peer, "ipBlock", "cidr") in {"0.0.0.0/0", "::/0"}:
                                broad += 1
            self.add(
                "PASS" if default_ingress and default_egress else "INFO",
                "k8s.network.default-deny",
                "Network",
                "Namespace default deny",
                f"defaultDenyIngress={default_ingress}; defaultDenyEgress={default_egress}; broadIpBlocks={broad}",
                "Use default-deny as a baseline and document intentional Internet-wide exceptions.",
                namespace,
                source=SOURCES["network"],
            )
        slices: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for endpoint_slice in items(self.raw.get("endpointslices")):
            meta = metadata(endpoint_slice)
            service = str((meta.get("labels") or {}).get("kubernetes.io/service-name") or "")
            if service:
                slices[(str(meta.get("namespace") or "default"), service)].append(endpoint_slice)
        services = {
            (str(metadata(service).get("namespace") or "default"), str(metadata(service).get("name") or "")): service
            for service in items(self.raw.get("services"))
        }
        for (namespace, name), service in services.items():
            spec = service.get("spec") or {}
            if not spec.get("selector") or spec.get("type") == "ExternalName":
                continue
            ready = 0
            total = 0
            for endpoint_slice in slices.get((namespace, name), []):
                for endpoint in endpoint_slice.get("endpoints") or []:
                    total += 1
                    ready += int(nested(endpoint, "conditions", "ready", fallback=True) is not False)
            self.add(
                "PASS" if ready else "WARN",
                "k8s.network.service-endpoints",
                "Network",
                "Service ready endpoints",
                f"ready={ready}; total={total}",
                "Validate selectors, pod readiness and EndpointSlice controller health.",
                namespace,
                f"Service/{name}",
                source=SOURCES["network"],
            )
        missing_backends: list[str] = []
        for ingress in items(self.raw.get("ingresses")):
            namespace = str(metadata(ingress).get("namespace") or "default")
            backends: list[dict[str, Any]] = []
            spec = ingress.get("spec") or {}
            if spec.get("defaultBackend"):
                backends.append(spec["defaultBackend"])
            for rule in spec.get("rules") or []:
                backends.extend(
                    path.get("backend") or {}
                    for path in nested(rule, "http", "paths", fallback=[]) or []
                )
            for backend in backends:
                service_name = str(nested(backend, "service", "name", fallback=""))
                if service_name and (namespace, service_name) not in services:
                    missing_backends.append(f"{namespace}/{service_name}")
        self.add(
            "CRIT" if missing_backends else "PASS",
            "k8s.network.ingress-backends",
            "Network",
            "Ingress backend references",
            f"missing={','.join(missing_backends[:20]) or 'none'}",
            "Create or correct every referenced Service before routing traffic.",
            source=SOURCES["network"],
        )

    def resilience(self) -> None:
        pdbs = items(self.raw.get("pdbs"))
        for item in self.workloads:
            namespace, kind, name = object_ref(item)
            ref = f"{kind}/{name}"
            template_meta, spec = pod_template(item)
            labels = template_meta.get("labels") or {}
            row = self.row_map.get((namespace, kind, name), {})
            replicas = int(row.get("replicas") or 0)
            for container in spec.get("containers") or []:
                cname = str(container.get("name") or "-")
                readiness = container.get("readinessProbe")
                liveness = container.get("livenessProbe")
                startup = container.get("startupProbe")
                if readiness and liveness and readiness == liveness:
                    self.add(
                        "WARN",
                        "k8s.reliability.identical-probes",
                        "Reliability",
                        "Identical readiness and liveness probes",
                        "Readiness and liveness definitions are identical.",
                        "Use readiness for traffic eligibility and liveness only for unrecoverable process failure.",
                        namespace,
                        ref,
                        cname,
                        SOURCES["probes"],
                    )
                if liveness and not startup and set(row.get("technologies") or []) & {"Java", ".NET", "Kafka", "RabbitMQ"}:
                    self.add(
                        "INFO",
                        "k8s.reliability.startup-probe",
                        "Reliability",
                        "Startup probe for slow-start runtime",
                        "Liveness exists without startupProbe.",
                        "Add a measured startupProbe when cold start can exceed the liveness budget.",
                        namespace,
                        ref,
                        cname,
                        SOURCES["probes"],
                    )
            grace = spec.get("terminationGracePeriodSeconds", 30)
            if int(grace or 0) <= 0:
                self.add(
                    "WARN",
                    "k8s.reliability.graceful-termination",
                    "Reliability",
                    "Termination grace period",
                    f"terminationGracePeriodSeconds={grace}",
                    "Allow enough time for endpoint removal, request drain and durable shutdown.",
                    namespace,
                    ref,
                    source=SOURCES["workloads"],
                )
            constraints = spec.get("topologySpreadConstraints") or []
            invalid_constraints = 0
            for constraint in constraints:
                valid_selector = selector_matches(
                    constraint.get("labelSelector") or {}, labels
                )
                valid_key = constraint.get("topologyKey") in {
                    "topology.kubernetes.io/zone",
                    "kubernetes.io/hostname",
                }
                valid_skew = int(constraint.get("maxSkew") or 0) == 1
                invalid_constraints += int(
                    not valid_selector or not valid_key or not valid_skew
                )
            if constraints:
                self.add(
                    "WARN" if invalid_constraints else "PASS",
                    "k8s.topology.constraint-semantics",
                    "Topology",
                    "Topology spread semantics",
                    f"constraints={len(constraints)}; invalidOrWeak={invalid_constraints}",
                    "Use matching selectors, maxSkew=1 and zone/hostname topology keys as appropriate.",
                    namespace,
                    ref,
                    source=SOURCES["workloads"],
                )
            matching = [
                pdb
                for pdb in pdbs
                if str(metadata(pdb).get("namespace") or "default") == namespace
                and selector_matches(nested(pdb, "spec", "selector", fallback={}), labels)
            ]
            for pdb in matching:
                pdb_name = str(metadata(pdb).get("name") or "-")
                disruptions = nested(pdb, "status", "disruptionsAllowed")
                desired_healthy = nested(pdb, "status", "desiredHealthy")
                current_healthy = nested(pdb, "status", "currentHealthy")
                min_available = percent_or_int(
                    nested(pdb, "spec", "minAvailable"), replicas
                )
                max_unavailable = percent_or_int(
                    nested(pdb, "spec", "maxUnavailable"), replicas
                )
                blocks_all = (
                    replicas > 1
                    and disruptions == 0
                    and current_healthy is not None
                    and desired_healthy is not None
                    and int(current_healthy) >= int(desired_healthy)
                )
                self.add(
                    "WARN" if blocks_all else "PASS",
                    "k8s.reliability.pdb-semantics",
                    "Reliability",
                    "PDB eviction semantics",
                    f"pdb={pdb_name}; replicas={replicas}; minAvailable={min_available}; maxUnavailable={max_unavailable}; disruptionsAllowed={disruptions}",
                    "Allow at least one voluntary disruption when quorum and availability permit.",
                    namespace,
                    ref,
                    source=SOURCES["workloads"],
                )
            if kind == "Deployment":
                strategy = nested(item, "spec", "strategy", fallback={})
                if strategy.get("type", "RollingUpdate") == "Recreate" and replicas > 1:
                    self.add(
                        "WARN",
                        "k8s.reliability.deployment-strategy",
                        "Reliability",
                        "Deployment update strategy",
                        "strategy.type=Recreate",
                        "Use a tested RollingUpdate strategy unless downtime is intentional.",
                        namespace,
                        ref,
                        source=SOURCES["workloads"],
                    )
                if nested(item, "spec", "progressDeadlineSeconds") is None:
                    self.add(
                        "INFO",
                        "k8s.reliability.progress-deadline",
                        "Reliability",
                        "Deployment progress deadline",
                        "progressDeadlineSeconds uses the default.",
                        "Set a release-specific deadline and alert on ProgressDeadlineExceeded.",
                        namespace,
                        ref,
                        source=SOURCES["workloads"],
                    )
            if kind == "StatefulSet":
                service_name = nested(item, "spec", "serviceName")
                claims = nested(item, "spec", "volumeClaimTemplates", fallback=[]) or []
                technology = set(row.get("technologies") or [])
                self.add(
                    "PASS" if service_name else "WARN",
                    "k8s.stateful.headless-service",
                    "Storage",
                    "StatefulSet governing service",
                    f"serviceName={service_name or 'absent'}",
                    "Use a valid governing headless Service for stable network identity.",
                    namespace,
                    ref,
                    source=SOURCES["storage"],
                )
                if technology & {"Kafka", "RabbitMQ", "PostgreSQL", "Redis"} and not claims:
                    self.add(
                        "WARN",
                        "k8s.stateful.persistent-storage",
                        "Storage",
                        "Stateful workload persistence",
                        "No volumeClaimTemplate was detected.",
                        "Validate durable storage, topology, reclaim policy and backup for stateful data.",
                        namespace,
                        ref,
                        source=SOURCES["storage"],
                    )
            if kind == "CronJob":
                cron = item.get("spec") or {}
                if cron.get("concurrencyPolicy", "Allow") == "Allow":
                    self.add(
                        "INFO",
                        "k8s.jobs.cron-concurrency",
                        "Reliability",
                        "CronJob concurrency",
                        "concurrencyPolicy=Allow",
                        "Use Forbid or Replace when overlapping executions are unsafe.",
                        namespace,
                        ref,
                        source=SOURCES["workloads"],
                    )
                if cron.get("startingDeadlineSeconds") is None:
                    self.add(
                        "INFO",
                        "k8s.jobs.cron-deadline",
                        "Reliability",
                        "CronJob start deadline",
                        "startingDeadlineSeconds is absent.",
                        "Set a deadline when stale delayed executions should be skipped.",
                        namespace,
                        ref,
                        source=SOURCES["workloads"],
                    )
            if kind == "Job":
                job = item.get("spec") or {}
                if job.get("ttlSecondsAfterFinished") is None:
                    self.add(
                        "INFO",
                        "k8s.jobs.ttl",
                        "Governance",
                        "Finished Job cleanup",
                        "ttlSecondsAfterFinished is absent.",
                        "Configure TTL cleanup or a controlled history-retention process.",
                        namespace,
                        ref,
                        source=SOURCES["workloads"],
                    )

    def autoscaling(self) -> None:
        hpa_targets: dict[tuple[str, str, str], dict[str, Any]] = {}
        for hpa in items(self.raw.get("hpas")):
            namespace = str(metadata(hpa).get("namespace") or "default")
            target = nested(hpa, "spec", "scaleTargetRef", fallback={})
            key = (namespace, str(target.get("kind") or "Deployment"), str(target.get("name") or ""))
            hpa_targets[key] = hpa
            spec = hpa.get("spec") or {}
            minimum = int(spec.get("minReplicas") or 1)
            maximum = int(spec.get("maxReplicas") or 0)
            limited, reason = condition(hpa, "ScalingLimited")
            active, active_reason = condition(hpa, "ScalingActive")
            severity = (
                "CRIT"
                if maximum < minimum
                else "WARN"
                if limited == "True" or active == "False"
                else "PASS"
            )
            self.add(
                severity,
                "k8s.autoscaling.hpa-health",
                "Autoscaling",
                "HPA semantic health",
                f"min={minimum}; max={maximum}; metrics={len(spec.get('metrics') or [])}; scalingLimited={limited}:{reason}; scalingActive={active}:{active_reason}",
                "Resolve metric errors and saturation; validate targets and stabilization policies.",
                namespace,
                f"HPA/{metadata(hpa).get('name', '-')}",
                source=SOURCES["autoscaling"],
            )
        for vpa in items(self.raw.get("vpas")):
            namespace = str(metadata(vpa).get("namespace") or "default")
            target = nested(vpa, "spec", "targetRef", fallback={})
            key = (namespace, str(target.get("kind") or "Deployment"), str(target.get("name") or ""))
            mode = str(
                nested(
                    vpa,
                    "spec",
                    "updatePolicy",
                    "updateMode",
                    fallback="Auto",
                )
            )
            hpa = hpa_targets.get(key)
            hpa_resource_metrics = [
                nested(metric, "resource", "name")
                for metric in nested(hpa or {}, "spec", "metrics", fallback=[]) or []
                if metric.get("type") in {"Resource", "ContainerResource"}
            ]
            conflict = bool(hpa and mode not in {"Off", "Initial"} and set(hpa_resource_metrics) & {"cpu", "memory"})
            self.add(
                "WARN" if conflict else "PASS",
                "k8s.autoscaling.vpa-hpa-conflict",
                "Autoscaling",
                "VPA and HPA compatibility",
                f"target={key[1]}/{key[2]}; updateMode={mode}; hpaResourceMetrics={','.join(filter(None, hpa_resource_metrics)) or 'none'}",
                "Avoid competing VPA updates and utilization-based HPA control on the same resources.",
                namespace,
                f"VPA/{metadata(vpa).get('name', '-')}",
                source=SOURCES["autoscaling"],
            )
        for scaled in items(self.raw.get("keda_scaledobjects")):
            namespace = str(metadata(scaled).get("namespace") or "default")
            ready, reason = condition(scaled, "Ready")
            active, active_reason = condition(scaled, "Active")
            spec = scaled.get("spec") or {}
            fallback = spec.get("fallback")
            min_replicas = int(spec.get("minReplicaCount") or 0)
            severity = "WARN" if ready == "False" else "PARTIAL" if ready == "Unknown" else "PASS"
            self.add(
                severity,
                "k8s.autoscaling.keda-health",
                "Autoscaling",
                "KEDA ScaledObject health",
                f"ready={ready}:{reason}; active={active}:{active_reason}; triggers={len(spec.get('triggers') or [])}; min={min_replicas}; fallback={bool(fallback)}",
                "Resolve trigger/metric authentication errors and define fallback for critical external metrics.",
                namespace,
                f"ScaledObject/{metadata(scaled).get('name', '-')}",
                source=SOURCES["autoscaling"],
            )

    def rbac_and_admission(self) -> None:
        for key in ("roles", "clusterroles"):
            for role in items(self.raw.get(key)):
                namespace, kind, name = object_ref(role)
                risks: set[str] = set()
                for rule in role.get("rules") or []:
                    verbs = set(map(str, rule.get("verbs") or []))
                    resources = set(map(str, rule.get("resources") or []))
                    if verbs & {"escalate", "bind", "impersonate"}:
                        risks.add("privilege-escalation-verbs")
                    if "secrets" in resources and verbs & {"get", "list", "watch"}:
                        risks.add("secret-read")
                    if resources & {"pods/exec", "pods/attach", "nodes/proxy"}:
                        risks.add("remote-execution-or-node-proxy")
                    if "*" in verbs or "*" in resources:
                        risks.add("wildcard")
                if risks:
                    self.add(
                        "CRIT" if "privilege-escalation-verbs" in risks else "WARN",
                        "k8s.rbac.high-risk-permissions",
                        "Security",
                        "High-risk RBAC permissions",
                        f"risks={','.join(sorted(risks))}",
                        "Scope verbs/resources/resourceNames and review every bound subject.",
                        namespace if key == "roles" else "-",
                        f"{kind}/{name}",
                        source=SOURCES["rbac"],
                    )
        services = {
            (str(metadata(item).get("namespace") or "default"), str(metadata(item).get("name") or ""))
            for item in items(self.raw.get("services"))
        }
        for key in ("validatingwebhooks", "mutatingwebhooks"):
            for config in items(self.raw.get(key)):
                _, kind, name = object_ref(config)
                for webhook in config.get("webhooks") or []:
                    webhook_name = str(webhook.get("name") or name)
                    service = nested(webhook, "clientConfig", "service", fallback={})
                    service_ref = (
                        str(service.get("namespace") or "default"),
                        str(service.get("name") or ""),
                    )
                    missing_service = bool(service.get("name")) and service_ref not in services
                    timeout = int(webhook.get("timeoutSeconds") or 10)
                    failure = str(webhook.get("failurePolicy") or "Fail")
                    side_effects = str(webhook.get("sideEffects") or "")
                    versions = set(map(str, webhook.get("admissionReviewVersions") or []))
                    problems = []
                    if missing_service:
                        problems.append("service-missing")
                    if timeout > 10:
                        problems.append("timeout>10s")
                    if side_effects not in {"None", "NoneOnDryRun"}:
                        problems.append("sideEffects")
                    if "v1" not in versions:
                        problems.append("admissionReview-v1-missing")
                    if failure == "Ignore":
                        problems.append("failurePolicy=Ignore")
                    self.add(
                        "CRIT" if missing_service else "WARN" if problems else "PASS",
                        "k8s.admission.webhook-reliability",
                        "Security",
                        "Admission webhook reliability",
                        f"problems={','.join(problems) or 'none'}; failurePolicy={failure}; timeout={timeout}",
                        "Keep webhook endpoints redundant, fast, TLS-valid and fail according to documented risk.",
                        workload=f"{kind}/{webhook_name}",
                        source=SOURCES["security"],
                    )

    def storage_and_dr(self) -> None:
        storage_classes = items(self.raw.get("storageclasses"))
        defaults = [
            item
            for item in storage_classes
            if any(
                (metadata(item).get("annotations") or {}).get(key) == "true"
                for key in (
                    "storageclass.kubernetes.io/is-default-class",
                    "storageclass.beta.kubernetes.io/is-default-class",
                )
            )
        ]
        pvcs = items(self.base.get("pvcs"))
        if pvcs:
            self.add(
                "PASS" if len(defaults) == 1 else "WARN",
                "k8s.storage.default-class",
                "Storage",
                "Default StorageClass",
                f"defaultClasses={len(defaults)}; storageClasses={len(storage_classes)}",
                "Maintain one intentional default StorageClass and validate topology/expansion settings.",
                source=SOURCES["storage"],
            )
        for storage_class in storage_classes:
            name = str(metadata(storage_class).get("name") or "-")
            mode = str(storage_class.get("volumeBindingMode") or "Immediate")
            provisioner = str(storage_class.get("provisioner") or "")
            zonal = any(token in provisioner.lower() for token in ("ebs", "pd.csi", "disk.csi"))
            if zonal and mode != "WaitForFirstConsumer":
                self.add(
                    "WARN",
                    "k8s.storage.topology-binding",
                    "Storage",
                    "Zonal volume binding mode",
                    f"provisioner={provisioner}; volumeBindingMode={mode}",
                    "Use WaitForFirstConsumer so volume topology follows Pod scheduling.",
                    workload=f"StorageClass/{name}",
                    source=SOURCES["storage"],
                )
        snapshots = items(self.raw.get("volumesnapshots"))
        not_ready = [
            f"{metadata(item).get('namespace')}/{metadata(item).get('name')}"
            for item in snapshots
            if nested(item, "status", "readyToUse") is not True
        ]
        if snapshots:
            self.add(
                "WARN" if not_ready else "PASS",
                "k8s.dr.volume-snapshot-health",
                "DR",
                "VolumeSnapshot readiness",
                f"snapshots={len(snapshots)}; notReady={len(not_ready)}",
                "Resolve snapshot errors and test restoration into an isolated environment.",
                source=SOURCES["dr"],
            )
        backups = items(self.raw.get("velero_backups"))
        completed = [
            item
            for item in backups
            if str(nested(item, "status", "phase", fallback="")).lower()
            in {"completed", "partiallyfailed"}
        ]
        failed = [
            item
            for item in backups
            if str(nested(item, "status", "phase", fallback="")).lower()
            in {"failed", "failedvalidation"}
        ]
        timestamps = [
            parse_time(
                nested(item, "status", "completionTimestamp")
                or metadata(item).get("creationTimestamp")
            )
            for item in completed
        ]
        timestamps = [value for value in timestamps if value]
        age_hours = (
            (dt.datetime.now(dt.timezone.utc) - max(timestamps)).total_seconds() / 3600
            if timestamps
            else None
        )
        if backups:
            self.add(
                "CRIT" if failed else "WARN" if age_hours is None or age_hours > 48 else "PASS",
                "k8s.dr.backup-health",
                "DR",
                "In-cluster backup evidence",
                f"backups={len(backups)}; completed={len(completed)}; failed={len(failed)}; latestAgeHours={round(age_hours, 1) if age_hours is not None else 'unknown'}",
                "Meet documented RPO and validate successful restore, not only backup completion.",
                source=SOURCES["dr"],
            )
        elif pvcs:
            self.add(
                "UNKNOWN",
                "k8s.dr.backup-coverage",
                "DR",
                "Backup and restore evidence",
                f"persistentClaims={len(pvcs)}; no Velero backup object collected; external backup may exist",
                "Provide AWS Backup, CSI snapshot or other backup/restore evidence with RPO/RTO.",
                source=SOURCES["dr"],
            )
        restores = items(self.raw.get("velero_restores"))
        if backups and not restores:
            self.add(
                "UNKNOWN",
                "k8s.dr.restore-test",
                "DR",
                "Restore-test evidence",
                "Backups exist but no in-cluster Restore object was collected.",
                "Run and document periodic isolated restore tests.",
                source=SOURCES["dr"],
            )

    def upgrade_and_supply_chain(self) -> None:
        deprecated: list[str] = []
        sources = [
            *self.workloads,
            *items(self.raw.get("ingresses")),
            *items(self.raw.get("hpas")),
            *items(self.raw.get("pdbs")),
            *items(self.raw.get("crds")),
            *items(self.raw.get("validatingwebhooks")),
            *items(self.raw.get("mutatingwebhooks")),
        ]
        for item in sources:
            api = str(item.get("apiVersion") or "")
            if api in DEPRECATED_APIS:
                namespace, kind, name = object_ref(item)
                deprecated.append(f"{api}:{namespace}/{kind}/{name}")
        self.add(
            "CRIT" if deprecated else "PASS",
            "k8s.upgrade.deprecated-api",
            "Upgrade",
            "Deprecated API objects",
            f"count={len(deprecated)}; sample={','.join(deprecated[:20]) or 'none'}",
            "Migrate stored manifests and controllers before upgrading Kubernetes.",
            source=SOURCES["upgrade"],
        )
        unavailable_apiservices = []
        for service in items(self.raw.get("apiservices")):
            available, reason = condition(service, "Available")
            if available != "True":
                unavailable_apiservices.append(
                    f"{metadata(service).get('name')}:{available}:{reason}"
                )
        self.add(
            "WARN" if unavailable_apiservices else "PASS",
            "k8s.upgrade-aggregation.api-services",
            "Upgrade",
            "Aggregated APIService health",
            f"unavailable={len(unavailable_apiservices)}; sample={','.join(unavailable_apiservices[:10]) or 'none'}",
            "Repair aggregated APIs and their backing Services before upgrades.",
            source=SOURCES["upgrade"],
        )
        reports = [
            *items(self.raw.get("policyreports")),
            *items(self.raw.get("clusterpolicyreports")),
        ]
        failures = 0
        for report in reports:
            summary = report.get("summary") or nested(report, "status", "summary", fallback={})
            failures += int(summary.get("fail") or summary.get("error") or 0)
        if reports:
            self.add(
                "WARN" if failures else "PASS",
                "k8s.security.policy-report",
                "Security",
                "Admission policy reports",
                f"reports={len(reports)}; failures={failures}",
                "Remediate current policy violations or register approved, expiring exceptions.",
                source=SOURCES["security"],
            )
        image_evidence = self.a.directory / "image-assessment.json"
        if image_evidence.is_file():
            try:
                image_data = json.loads(image_evidence.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                image_data = {}
            vulnerable = int(nested(image_data, "summary", "critical", fallback=0) or 0)
            unsigned = int(nested(image_data, "summary", "unsigned", fallback=0) or 0)
            self.add(
                "CRIT" if vulnerable else "WARN" if unsigned else "PASS",
                "k8s.supply.image-evidence",
                "SupplyChain",
                "Image SBOM/signature/vulnerability evidence",
                f"criticalVulnerabilities={vulnerable}; unsigned={unsigned}",
                "Block unapproved critical vulnerabilities and enforce trusted provenance.",
                source=SOURCES["supply"],
            )
        elif self.workloads:
            self.add(
                "UNKNOWN",
                "k8s.supply.image-evidence",
                "SupplyChain",
                "Image SBOM/signature/vulnerability evidence",
                "Only image references were assessed; registry contents and signatures were not inspected.",
                "Provide opt-in registry evidence from ECR scanning, SBOM and signature verification.",
                source=SOURCES["supply"],
            )

    def technology_operators(self) -> None:
        for kafka in items(self.raw.get("strimzi_kafkas")):
            namespace, kind, name = object_ref(kafka)
            replicas = nested(kafka, "spec", "kafka", "replicas")
            config = nested(kafka, "spec", "kafka", "config", fallback={})
            min_isr = config.get("min.insync.replicas") if isinstance(config, dict) else None
            if replicas is None:
                severity = "PARTIAL"
            elif int(replicas) < 3:
                severity = "WARN"
            else:
                severity = "PASS"
            self.add(
                severity,
                "k8s.kafka.operator-topology",
                "Runtime",
                "Strimzi Kafka topology",
                f"replicas={replicas if replicas is not None else 'node-pools-or-unknown'}; min.insync.replicas={min_isr}",
                "Validate KafkaNodePools, odd controller quorum, rack awareness, replication and durable storage.",
                namespace,
                f"{kind}/{name}",
                source="https://strimzi.io/docs/operators/latest/",
            )
        for rabbit in items(self.raw.get("rabbitmq_clusters")):
            namespace, kind, name = object_ref(rabbit)
            replicas = int(nested(rabbit, "spec", "replicas", fallback=1) or 1)
            storage = nested(rabbit, "spec", "persistence", "storage")
            self.add(
                "PASS" if replicas >= 3 and replicas % 2 == 1 and storage else "WARN",
                "k8s.rabbitmq.operator-topology",
                "Runtime",
                "RabbitMQ Cluster Operator topology",
                f"replicas={replicas}; odd={replicas % 2 == 1}; persistentStorage={bool(storage)}",
                "Use an odd quorum of persistent nodes spread across failure domains and test recovery.",
                namespace,
                f"{kind}/{name}",
                source="https://www.rabbitmq.com/kubernetes/operator/operator-using.html",
            )
        for cluster in items(self.raw.get("cnpg_clusters")):
            namespace, kind, name = object_ref(cluster)
            instances = int(nested(cluster, "spec", "instances", fallback=1) or 1)
            storage = nested(cluster, "spec", "storage", fallback={})
            self.add(
                "PASS" if instances >= 3 and storage else "WARN",
                "k8s.postgresql.cnpg-topology",
                "Runtime",
                "CloudNativePG topology",
                f"instances={instances}; storageConfigured={bool(storage)}",
                "Use resilient instances, tested backups/PITR and topology-aware scheduling.",
                namespace,
                f"{kind}/{name}",
                source="https://cloudnative-pg.io/documentation/current/",
            )

    def collection_coverage(self) -> None:
        coverage = nested(self.a.collection, "resources", fallback={}) or {}
        unavailable = [
            f"{key}:{entry.get('reason', '')}"
            for key, entry in coverage.items()
            if entry.get("state") == "UNAVAILABLE"
        ]
        budget = nested(self.a.collection, "requestBudget", fallback={}) or {}
        if unavailable:
            self.add(
                "PARTIAL",
                "assessment.coverage.deep-resources",
                "Coverage",
                "Deep resource collection",
                f"unavailable={len(unavailable)}; sample={'; '.join(unavailable[:10])}",
                "Grant missing read permissions or document exclusions; do not interpret missing data as compliance.",
                confidence="LOW",
            )
        if budget.get("state") == "PARTIAL":
            self.add(
                "PARTIAL",
                "assessment.coverage.api-budget",
                "Coverage",
                "API collection budget",
                f"reason={budget.get('reason')}; requests={budget.get('requests')}; responseBytes={budget.get('responseBytes')}",
                "Resume the collection with a controlled larger budget or narrower namespace scope.",
                confidence="LOW",
            )

    def run(self) -> dict[str, Any]:
        self.nodes()
        self.namespace_security()
        self.extended_container_security()
        self.network_and_endpoints()
        self.resilience()
        self.autoscaling()
        self.rbac_and_admission()
        self.storage_and_dr()
        self.upgrade_and_supply_chain()
        self.technology_operators()
        self.collection_coverage()
        return {
            "checksAdded": sum(self.stats.values()),
            "counts": dict(self.stats),
            "domains": [
                "nodes",
                "pod-security",
                "network",
                "reliability",
                "autoscaling",
                "rbac-admission",
                "storage-dr",
                "upgrade-supply-chain",
                "technology-operators",
                "coverage",
            ],
        }


def apply_semantic_assessment(assessment: Any) -> dict[str, Any]:
    return SemanticRules(assessment).run()
