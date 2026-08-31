#!/usr/bin/env python3
"""Operational recommendations derived from sanitized assessment evidence."""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from lifecycle_catalog import assess as assess_lifecycle, catalog_metadata, load_catalog


SYSTEM_NAMESPACES = {
    "kube-system", "kube-public", "kube-node-lease", "calico-system",
    "calico-apiserver", "tigera-operator", "monitoring", "observability",
    "argocd", "istio-system", "cert-manager", "ingress-nginx",
}


def load(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def items(value: Any) -> list[dict[str, Any]]:
    return [x for x in (value.get("items", []) if isinstance(value, dict) else []) if isinstance(x, dict)]


def platform(nodes: list[dict[str, Any]], aws: dict[str, Any]) -> str:
    text = " ".join(str((x.get("spec") or {}).get("providerID", "")) for x in nodes).lower()
    labels = " ".join(" ".join((x.get("metadata") or {}).get("labels", {}).keys()) for x in nodes).lower()
    if (aws or {}).get("state") not in {None, "", "N/A", "NOT_APPLICABLE"} or "aws:///" in text: return "eks"
    if "azure:///" in text or "kubernetes.azure.com" in labels: return "aks"
    if "gce://" in text or "cloud.google.com/gke" in labels: return "gke"
    return "generic-kubernetes"


def number(value: Any) -> float | None:
    try:
        parsed = float(value)
        return parsed if parsed >= 0 else None
    except (TypeError, ValueError):
        return None


def cpu_cores(value: Any) -> float | None:
    text = str(value or "").strip()
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)(n|u|m)?", text)
    if not match:
        return None
    parsed = float(match.group(1))
    return parsed * {"n": 1e-9, "u": 1e-6, "m": 1e-3, None: 1.0}[match.group(2)]


def memory_bytes(value: Any) -> float | None:
    text = str(value or "").strip()
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)((?:[EPTGMK]i)|[eptgmkEPTGMK])?", text)
    if not match:
        return None
    parsed = float(match.group(1))
    suffix = match.group(2) or ""
    binary = {"Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "Ti": 2**40, "Pi": 2**50, "Ei": 2**60}
    decimal = {"k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9, "T": 1e12, "P": 1e15, "E": 1e18, "m": 1e-3}
    return parsed * binary.get(suffix, decimal.get(suffix, 1.0))


def percent(value: float | None, reference: float | None) -> float | None:
    if value is None or reference in (None, 0):
        return None
    return round(100.0 * value / reference, 2)


def add_quantity(target: dict[str, float], usage: dict[str, Any]) -> None:
    cpu = cpu_cores(usage.get("cpu"))
    memory = memory_bytes(usage.get("memory"))
    if cpu is not None:
        target["cpuCores"] += cpu
    if memory is not None:
        target["memoryBytes"] += memory


def pod_requests(pod: dict[str, Any]) -> dict[str, float]:
    spec = pod.get("spec") or {}

    def total(containers: list[dict[str, Any]]) -> dict[str, float]:
        result = {"cpuCores": 0.0, "memoryBytes": 0.0}
        for container in containers:
            add_quantity(result, ((container.get("resources") or {}).get("requests") or {}))
        return result

    regular = total(spec.get("containers") or [])
    restartable = {"cpuCores": 0.0, "memoryBytes": 0.0}
    init_peak = {"cpuCores": 0.0, "memoryBytes": 0.0}
    for container in spec.get("initContainers") or []:
        current = total([container])
        if container.get("restartPolicy") == "Always":
            for key in restartable:
                restartable[key] += current[key]
                init_peak[key] = max(init_peak[key], restartable[key])
        else:
            for key in restartable:
                init_peak[key] = max(init_peak[key], restartable[key] + current[key])
    pod_level = {"cpuCores": 0.0, "memoryBytes": 0.0}
    add_quantity(pod_level, ((spec.get("resources") or {}).get("requests") or {}))
    overhead = {"cpuCores": 0.0, "memoryBytes": 0.0}
    add_quantity(overhead, spec.get("overhead") or {})
    return {
        key: max(regular[key] + restartable[key], init_peak[key], pod_level[key]) + overhead[key]
        for key in regular
    }


def pod_category(pod: dict[str, Any]) -> str:
    metadata = pod.get("metadata") or {}
    owners = metadata.get("ownerReferences") or []
    if any(str(owner.get("kind")) == "DaemonSet" for owner in owners):
        return "daemonSets"
    if str(metadata.get("namespace") or "default") in SYSTEM_NAMESPACES:
        return "kubernetesPods"
    return "workloads"


def condition_map(node: dict[str, Any]) -> dict[str, str]:
    return {
        str(item.get("type")): str(item.get("status"))
        for item in (node.get("status") or {}).get("conditions") or []
        if item.get("type")
    }


def node_health(
    nodes: list[dict[str, Any]], pods: list[dict[str, Any]],
    node_metrics: list[dict[str, Any]], pod_metrics: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build provider-neutral node health from Kubernetes and Metrics APIs."""
    metrics_by_node = {
        str((item.get("metadata") or {}).get("name")): item for item in node_metrics
    }
    pod_metrics_by_key = {
        (str((item.get("metadata") or {}).get("namespace") or "default"),
         str((item.get("metadata") or {}).get("name"))): item
        for item in pod_metrics
    }
    pods_by_node: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for pod in pods:
        node_name = str((pod.get("spec") or {}).get("nodeName") or "")
        phase = str((pod.get("status") or {}).get("phase") or "")
        if node_name and phase not in {"Succeeded", "Failed"}:
            pods_by_node[node_name].append(pod)

    rows = []
    for node in nodes:
        metadata, status = node.get("metadata") or {}, node.get("status") or {}
        name = str(metadata.get("name") or "-")
        capacity, allocatable = status.get("capacity") or {}, status.get("allocatable") or {}
        capacity_cpu, allocatable_cpu = cpu_cores(capacity.get("cpu")), cpu_cores(allocatable.get("cpu"))
        capacity_memory, allocatable_memory = memory_bytes(capacity.get("memory")), memory_bytes(allocatable.get("memory"))
        pod_capacity = number(allocatable.get("pods") or capacity.get("pods"))
        scheduled = pods_by_node.get(name, [])

        categories = {
            key: {"cpuCores": 0.0, "memoryBytes": 0.0}
            for key in ("kubernetesPods", "daemonSets", "workloads")
        }
        requests = {"cpuCores": 0.0, "memoryBytes": 0.0}
        expected_metrics = 0
        observed_metrics = 0
        for pod in scheduled:
            request = pod_requests(pod)
            requests["cpuCores"] += request["cpuCores"]
            requests["memoryBytes"] += request["memoryBytes"]
            if str((pod.get("status") or {}).get("phase")) != "Running":
                continue
            expected_metrics += 1
            key = (str((pod.get("metadata") or {}).get("namespace") or "default"),
                   str((pod.get("metadata") or {}).get("name")))
            metric = pod_metrics_by_key.get(key)
            if not metric:
                continue
            container_metrics = metric.get("containers") or []
            if not container_metrics or any(
                cpu_cores((container.get("usage") or {}).get("cpu")) is None
                or memory_bytes((container.get("usage") or {}).get("memory")) is None
                for container in container_metrics
            ):
                continue
            observed_metrics += 1
            category = categories[pod_category(pod)]
            for container in container_metrics:
                add_quantity(category, container.get("usage") or {})

        metric = metrics_by_node.get(name)
        metric_usage = (metric or {}).get("usage") or {}
        node_cpu_usage = cpu_cores(metric_usage.get("cpu"))
        node_memory_usage = memory_bytes(metric_usage.get("memory"))
        node_metric_available = bool(metric and node_cpu_usage is not None and node_memory_usage is not None)
        node_usage = {"cpuCores": node_cpu_usage or 0.0, "memoryBytes": node_memory_usage or 0.0}
        observed = {
            key: sum(category[key] for category in categories.values())
            for key in ("cpuCores", "memoryBytes")
        }
        overhead = {
            key: max(0.0, node_usage[key] - observed[key]) if node_metric_available else None
            for key in observed
        }
        headroom = {
            "cpuCores": max(0.0, allocatable_cpu - node_usage["cpuCores"]) if node_metric_available and allocatable_cpu is not None else None,
            "memoryBytes": max(0.0, allocatable_memory - node_usage["memoryBytes"]) if node_metric_available and allocatable_memory is not None else None,
        }
        reserved = {
            "cpuCores": max(0.0, capacity_cpu - allocatable_cpu) if capacity_cpu is not None and allocatable_cpu is not None else None,
            "memoryBytes": max(0.0, capacity_memory - allocatable_memory) if capacity_memory is not None and allocatable_memory is not None else None,
        }
        conditions = condition_map(node)
        ready_status = conditions.get("Ready")
        ready = True if ready_status == "True" else False if ready_status in {"False", "Unknown"} else None
        pressure = sorted(key for key in ("MemoryPressure", "DiskPressure", "PIDPressure", "NetworkUnavailable") if conditions.get(key) == "True")
        cpu_usage_pct = percent(node_usage["cpuCores"], allocatable_cpu) if node_metric_available else None
        memory_usage_pct = percent(node_usage["memoryBytes"], allocatable_memory) if node_metric_available else None
        cpu_request_pct = percent(requests["cpuCores"], allocatable_cpu)
        memory_request_pct = percent(requests["memoryBytes"], allocatable_memory)
        pod_pct = percent(float(len(scheduled)), pod_capacity)
        critical, warnings, partial = [], [], []
        if ready is False:
            critical.append(f"Node Ready={ready_status}")
        elif ready is None:
            partial.append("Condição Ready indisponível")
        required_conditions = {"MemoryPressure", "DiskPressure", "PIDPressure"}
        missing_conditions = sorted(required_conditions.difference(conditions))
        if missing_conditions:
            partial.append("Condições indisponíveis: " + ", ".join(missing_conditions))
        if pressure:
            critical.append("Condição de pressão: " + ", ".join(pressure))
        if allocatable_cpu is None or allocatable_memory is None or pod_capacity is None:
            partial.append("Capacity/allocatable incompleto")
        for label, value, warning, critical_at in (
            ("CPU em uso", cpu_usage_pct, 85, 95), ("memória em uso", memory_usage_pct, 80, 90),
            ("requests de CPU", cpu_request_pct, 85, 100), ("requests de memória", memory_request_pct, 85, 100),
            ("densidade de Pods", pod_pct, 80, 95),
        ):
            if value is None:
                continue
            if value >= critical_at:
                critical.append(f"{label} em {value:.1f}%")
            elif value >= warning:
                warnings.append(f"{label} em {value:.1f}%")
        if not node_metric_available:
            partial.append("Metrics API sem uso total do node")
        pod_coverage = percent(float(observed_metrics), float(expected_metrics)) if expected_metrics else 100.0
        if expected_metrics and observed_metrics < expected_metrics:
            partial.append(f"Pod metrics cobrem {observed_metrics}/{expected_metrics} Pods Running")
        state = "CRIT" if critical else "WARN" if warnings else "PARTIAL" if partial else "PASS"
        diagnosis = critical + warnings + partial or ["Node Ready, sem pressão e dentro dos thresholds avaliados"]
        info = status.get("nodeInfo") or {}
        rows.append({
            "node": name, "state": state, "ready": ready, "pressureConditions": pressure,
            "diagnosis": diagnosis,
            "runtime": info.get("containerRuntimeVersion", "UNKNOWN"),
            "os": info.get("osImage", "UNKNOWN"),
            "capacity": {"cpuCores": capacity_cpu, "memoryBytes": capacity_memory, "pods": pod_capacity},
            "allocatable": {"cpuCores": allocatable_cpu, "memoryBytes": allocatable_memory, "pods": pod_capacity},
            "nodeReserve": reserved,
            "usage": {
                "cpu": {"value": node_usage["cpuCores"] if node_metric_available else None, "percent": cpu_usage_pct},
                "memory": {"value": node_usage["memoryBytes"] if node_metric_available else None, "percent": memory_usage_pct},
                "pods": {"value": len(scheduled), "percent": pod_pct},
                "requests": {**requests, "cpuPercent": cpu_request_pct, "memoryPercent": memory_request_pct},
                "breakdown": {**categories, "nodeOverheadUnattributed": overhead, "headroom": headroom},
            },
            "evidence": {
                "node": "KubernetesAPI", "metrics": "MetricsAPI" if node_metric_available else "EVIDENCE_UNAVAILABLE",
                "nodeMetricsTimestamp": metric.get("timestamp") if metric else None,
                "window": metric.get("window") if metric else None,
                "runningPodsExpected": expected_metrics, "runningPodsObserved": observed_metrics,
                "podMetricsCoveragePercent": pod_coverage,
            },
        })
    counts = Counter(item["state"] for item in rows)
    metrics_nodes = sum(item["evidence"]["metrics"] == "MetricsAPI" for item in rows)
    overall = "EVIDENCE_UNAVAILABLE" if not rows else "CRIT" if counts["CRIT"] else "WARN" if counts["WARN"] else "PARTIAL" if counts["PARTIAL"] else "PASS"
    return {
        "state": overall,
        "summary": {
            "nodes": len(rows), "critical": counts["CRIT"], "warnings": counts["WARN"],
            "partial": counts["PARTIAL"], "passed": counts["PASS"],
            "metricsNodes": metrics_nodes, "metricsCoveragePercent": percent(float(metrics_nodes), float(len(rows))) if rows else 0.0,
        },
        "items": rows,
        "thresholds": {"cpuWarnPercent": 85, "cpuCriticalPercent": 95, "memoryWarnPercent": 80, "memoryCriticalPercent": 90, "requestsWarnPercent": 85, "requestsCriticalPercent": 100, "podsWarnPercent": 80, "podsCriticalPercent": 95},
        "notice": "Uso observado vem da Metrics API. Node overhead / não atribuído é a diferença entre o uso total do node e os Pods observados; pode incluir sistema operacional, kubelet, container runtime e lacunas de métricas. Requests e reserva não representam consumo real.",
    }


def diagnostics(events: list[dict[str, Any]], pods: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for event in events:
        meta, involved = event.get("metadata") or {}, event.get("regarding") or event.get("involvedObject") or {}
        reason = str(event.get("reason") or "Unknown")
        event_type = str(event.get("type") or "Normal")
        key = (str(meta.get("namespace") or involved.get("namespace") or "-"), str(involved.get("kind") or "-"), str(involved.get("name") or "-"), reason, event_type)
        row = grouped.setdefault(key, {"namespace": key[0], "kind": key[1], "resource": key[2], "reason": reason, "type": event_type, "count": 0, "lastSeen": event.get("eventTime") or event.get("lastTimestamp") or meta.get("creationTimestamp") or "", "recommendation": event_recommendation(reason)})
        row["count"] += int(event.get("count") or 1)
        row["lastSeen"] = max(str(row["lastSeen"]), str(event.get("eventTime") or event.get("lastTimestamp") or ""))
    pod_states = []
    for pod in pods:
        meta, status = pod.get("metadata") or {}, pod.get("status") or {}
        statuses = (status.get("containerStatuses") or []) + (status.get("initContainerStatuses") or [])
        reasons = []
        for state in statuses:
            current, previous = (state.get("state") or {}), (state.get("lastState") or {})
            reasons.extend(str(x.get("reason")) for x in [current.get("waiting") or {}, current.get("terminated") or {}, previous.get("terminated") or {}] if x.get("reason"))
        restarts = sum(int(x.get("restartCount") or 0) for x in statuses)
        if status.get("phase") not in {"Running", "Succeeded"} or reasons or restarts:
            pod_states.append({"namespace": meta.get("namespace", "-"), "pod": meta.get("name", "-"), "phase": status.get("phase", "Unknown"), "restarts": restarts, "reasons": sorted(set(reasons)), "node": (pod.get("spec") or {}).get("nodeName", "-")})
    rows = sorted(grouped.values(), key=lambda x: (x["type"] != "Warning", -x["count"], x["namespace"], x["resource"]))
    return {"summary": {"groups": len(rows), "warnings": sum(x["type"] == "Warning" for x in rows), "affectedPods": len(pod_states)}, "events": rows, "podStates": pod_states}


def event_recommendation(reason: str) -> str:
    value = reason.lower()
    mapping = [("schedul", "Revisar requests, taints, affinity, quotas e capacidade dos nodes."), ("image", "Validar referência da imagem, registry, credenciais e conectividade."), ("mount", "Validar PVC, CSI, permissões e disponibilidade do storage."), ("probe", "Revisar probes, tempo de startup, dependências e recursos."), ("oom", "Revisar working set, limite de memória e comportamento do runtime."), ("policy", "Revisar a policy de admission e corrigir o controller de origem."), ("evict", "Revisar pressão do node, requests, prioridades e políticas de eviction.")]
    return next((text for token, text in mapping if token in value), "Correlacionar o Event com o controller, Pod, node e dependências.")


def versions(directory: Path, nodes: list[dict[str, Any]], workloads: list[dict[str, Any]], technologies: list[dict[str, Any]], detected: str, cloud: dict[str, Any]) -> dict[str, Any]:
    rows = []
    catalog = load_catalog()
    catalog_info = catalog_metadata(catalog)
    cloud_cluster = cloud.get("cluster") or {}
    cloud_lifecycle = cloud.get("lifecycle") or {}
    control_plane_version = cloud_cluster.get("version")
    if control_plane_version:
        rows.append({
            "component": "Control plane", "name": detected, "version": control_plane_version,
            "runtime": "managed" if detected in {"eks", "aks", "gke"} else "UNKNOWN", "os": "N/A", "kernel": "N/A",
            "state": "DETECTED", "source": "CloudProviderAPI",
            "supportState": cloud_lifecycle.get("supportState", "UNKNOWN"),
            "supportUntil": cloud_lifecycle.get("supportUntil", "UNKNOWN"),
            "daysRemaining": cloud_lifecycle.get("daysRemaining"),
        })
    else:
        kubernetes = load(directory / "kubernetes-version.json", {})
        server_version = (kubernetes.get("serverVersion") or {}).get("gitVersion") if isinstance(kubernetes, dict) else None
        if server_version:
            lifecycle = assess_lifecycle(server_version, "generic-kubernetes", catalog=catalog)
            rows.append({"component": "Control plane", "name": "Kubernetes API", "version": server_version, "runtime": "UNKNOWN", "os": "N/A", "kernel": "N/A", "state": "DETECTED", "source": "KubernetesAPI", **{key: lifecycle.get(key) for key in ("supportState", "supportUntil", "daysRemaining")}})
    node_versions = Counter()
    for node in nodes:
        meta, info = node.get("metadata") or {}, (node.get("status") or {}).get("nodeInfo") or {}
        lifecycle = assess_lifecycle(info.get("kubeletVersion"), detected, support_type=str(cloud_cluster.get("supportType") or ""), release_channel=str(cloud_cluster.get("releaseChannel") or ""), catalog=catalog)
        row = {"component": "Node", "name": meta.get("name", "-"), "version": info.get("kubeletVersion", "UNKNOWN"), "runtime": info.get("containerRuntimeVersion", "UNKNOWN"), "os": info.get("osImage", "UNKNOWN"), "kernel": info.get("kernelVersion", "UNKNOWN"), "state": "DETECTED", "source": "KubernetesAPI", **{key: lifecycle.get(key) for key in ("supportState", "supportUntil", "daysRemaining")}}
        rows.append(row); node_versions[str(row["version"])] += 1
    images: dict[str, set[str]] = defaultdict(set)
    for workload in workloads:
        for container in workload.get("containers") or []:
            image = str(container.get("image") or "")
            if image:
                component = image.split("/")[-1].split(":")[0].split("@")[0]
                version = image.rsplit(":", 1)[1] if ":" in image.rsplit("/", 1)[-1] else ("digest" if "@sha256:" in image else "UNKNOWN")
                images[component].add(version)
    for name, detected in sorted(images.items()):
        rows.append({"component": "Container image", "name": name, "version": ", ".join(sorted(detected)), "runtime": "-", "os": "-", "kernel": "-", "state": "UNKNOWN" if "UNKNOWN" in detected else "DETECTED", "source": "KubernetesAPI", "supportState": "EVIDENCE_UNAVAILABLE", "supportUntil": "N/A", "daysRemaining": None, "lifecycleReason": "Tag detectada, mas não existe lifecycle confiável para esta imagem no catálogo."})
    for technology in technologies:
        if technology.get("state") == "DETECTED" and not any(x["name"].lower() == str(technology.get("name", "")).lower() for x in rows):
            rows.append({"component": "Technology", "name": technology.get("name"), "version": "UNKNOWN", "runtime": "-", "os": "-", "kernel": "-", "state": "UNKNOWN", "source": "Heuristic", "supportState": "EVIDENCE_UNAVAILABLE", "supportUntil": "N/A", "daysRemaining": None, "lifecycleReason": "Tecnologia detectada por heurística, sem versão ou lifecycle comprovável."})
    skew = len(node_versions) > 1
    return {"summary": {"components": len(rows), "unknownVersions": sum(x["version"] == "UNKNOWN" for x in rows), "nodeVersionSkew": skew, "endOfSupport": sum(x.get("supportState") == "END_OF_SUPPORT" for x in rows), "lifecycleUnknown": sum(str(x.get("supportState", "UNKNOWN")).startswith("UNKNOWN") for x in rows), "lifecycleEvidenceUnavailable": sum(x.get("supportState") == "EVIDENCE_UNAVAILABLE" for x in rows)}, "items": rows, "catalog": catalog_info, "notice": "Lifecycle/EOL usa catálogo oficial versionado e evidência regional do provider. UNKNOWN indica versão/estado indeterminado; EVIDENCE_UNAVAILABLE indica componente detectado sem fonte de lifecycle confiável."}


def manifest_quality(workloads: list[dict[str, Any]], findings: list[dict[str, Any]]) -> dict[str, Any]:
    accepted = {"Security", "SupplyChain", "Reliability", "Scheduling", "Autoscaling", "Storage", "Network", "Configuration", "PodHealth"}
    rows, seen = [], set()
    for finding in findings:
        if finding.get("category") not in accepted or finding.get("severity") not in {"CRIT", "WARN", "INFO", "UNKNOWN", "PARTIAL"}: continue
        key = (finding.get("ruleId"), finding.get("resourceKey"))
        if key in seen: continue
        seen.add(key)
        rows.append({"ruleId": finding.get("ruleId"), "severity": finding.get("severity"), "category": finding.get("category"), "namespace": finding.get("namespace"), "resource": finding.get("workload"), "container": finding.get("container"), "check": finding.get("check"), "evidence": finding.get("detail"), "recommendation": finding.get("recommendation")})
    return {"summary": {"resources": len(workloads), "issues": len(rows), "critical": sum(x["severity"] == "CRIT" for x in rows), "warnings": sum(x["severity"] == "WARN" for x in rows)}, "findings": rows, "notice": "Validação feita sobre objetos retornados pela Kubernetes API; não substitui schema validation no pipeline antes do deploy."}


def tuning(capacity: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for item in capacity:
        assessment = " ".join(item.get("assessment") or []).lower()
        action = "increase" if any(x in assessment for x in ("insuf", "thrott", "oom", "aument")) else "decrease" if any(x in assessment for x in ("superdimension", "reduz", "econom")) else "keep"
        if str(item.get("confidence", "")).upper() in {"LOW", "UNKNOWN", ""}: action = "insufficient evidence"
        rows.append({**item, "action": action, "automaticChange": False})
    return {"summary": dict(Counter(x["action"] for x in rows)), "recommendations": rows, "notice": "Propostas exigem teste de carga e validação humana; nenhuma alteração é aplicada."}


def best_practices(detected: str, findings: list[dict[str, Any]], nodes: list[dict[str, Any]], cloud: dict[str, Any] | None = None) -> dict[str, Any]:
    cloud = cloud or {}
    rows = []
    domains = {"Security": "Security", "SupplyChain": "Security", "Reliability": "Reliability", "Health": "Operations", "Scheduling": "Scalability", "Autoscaling": "Scalability", "Network": "Networking", "Storage": "Reliability", "Cost": "Cost"}
    for finding in findings:
        domain = domains.get(str(finding.get("category")))
        if not domain or finding.get("severity") not in {"CRIT", "WARN", "UNKNOWN", "PARTIAL"}: continue
        rows.append({"ruleId": "bestpractice." + str(finding.get("ruleId") or finding.get("id")), "provider": "generic", "domain": domain, "status": finding.get("severity"), "applicability": finding.get("applicability", "APPLICABLE"), "responsibility": "CUSTOMER", "resource": finding.get("workload"), "evidence": finding.get("detail"), "recommendation": finding.get("recommendation")})
    provider_rules = {
        "eks": [("eks.identity.pod-identity", "Security", "Validar EKS Pod Identity/IRSA e evitar credenciais estáticas."), ("eks.network.ip-capacity", "Networking", "Validar capacidade de IP, VPC CNI e prefix delegation."), ("eks.upgrade.addons", "Operations", "Validar compatibilidade e lifecycle dos managed add-ons."), ("eks.reliability.multi-az", "Reliability", "Distribuir nodes e workloads entre Availability Zones.")],
        "aks": [("aks.identity.workload", "Security", "Validar Microsoft Entra Workload ID e Azure RBAC."), ("aks.nodepools.system", "Reliability", "Validar separação e resiliência do system node pool."), ("aks.upgrade.channels", "Operations", "Validar canais de upgrade e manutenção."), ("aks.network.egress", "Networking", "Validar modelo de rede, egress e capacidade de endereços.")],
        "gke": [("gke.identity.workload", "Security", "Validar Workload Identity Federation for GKE."), ("gke.release.channel", "Operations", "Validar release channel e estratégia de upgrade."), ("gke.shielded.nodes", "Security", "Validar Shielded GKE Nodes quando aplicável."), ("gke.network.private", "Networking", "Validar private cluster, egress e políticas de rede.")],
    }
    evidenced = {str(item.get("ruleId")): item for item in cloud.get("bestPractices") or [] if isinstance(item, dict)}
    for provider, rules in provider_rules.items():
        for rule_id, domain, recommendation in rules:
            applicable = provider == detected
            evidence = evidenced.get(f"bestpractice.{rule_id}") if applicable else None
            if evidence:
                rows.append({**evidence, "provider": provider, "domain": evidence.get("domain") or domain})
            else:
                rows.append({"ruleId": f"bestpractice.{rule_id}", "provider": provider, "domain": domain, "status": "MANUAL" if applicable else "N/A", "applicability": "MANUAL_REVIEW" if applicable else "NOT_APPLICABLE", "responsibility": "SHARED" if applicable else "CLOUD_PROVIDER", "resource": "cluster", "evidence": "Provider identificado; evidência da Cloud Provider API indisponível." if applicable else f"Plataforma detectada: {detected}", "recommendation": recommendation})
    return {"platform": detected, "cloudEvidenceState": cloud.get("state", "N/A"), "summary": dict(Counter(x["status"] for x in rows)), "rules": rows, "notice": "Recomendações baseadas em evidência disponível; não representam certificação do provider."}


SECRET = re.compile(r'''(?ix)\b(authorization|password|passwd|token|secret|api[_-]?key|cookie|client[_-]?secret|connection[_-]?string)\b(["']?\s*[:=]\s*["']?)([^\s,;}"']+)''')
AUTH_SCHEME = re.compile(r"(?i)\b(?:bearer|basic)\s+[a-z0-9._~+/=-]+")
JWT = re.compile(r"\beyJ[a-zA-Z0-9_-]{8,}\.[a-zA-Z0-9_-]{8,}(?:\.[a-zA-Z0-9_-]{8,})?\b")
AWS_ACCESS_KEY = re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")
URL_CREDENTIALS = re.compile(r"(?i)(https?://)([^\s/@:]+):([^\s/@]+)@")


def redact_log_text(value: str) -> str:
    value = AUTH_SCHEME.sub("Bearer [REDACTED]", value)
    value = JWT.sub("[REDACTED-JWT]", value)
    value = AWS_ACCESS_KEY.sub("[REDACTED-AWS-KEY]", value)
    value = URL_CREDENTIALS.sub(r"\1[REDACTED]@", value)
    return SECRET.sub(r"\1\2[REDACTED]", value)


def utf8_prefix(value: str, max_bytes: int) -> tuple[str, bool]:
    encoded = value.encode("utf-8", "replace")
    if len(encoded) <= max_bytes:
        return value, False
    return encoded[:max_bytes].decode("utf-8", "ignore"), True


def sanitized_logs() -> dict[str, Any]:
    enabled = os.getenv("ASSESSMENT_INCLUDE_LOGS", "0") == "1"
    targets = [x.strip() for x in os.getenv("ASSESSMENT_LOG_TARGETS", "").split(",") if x.strip()]
    limit = min(max(int(os.getenv("ASSESSMENT_LOG_MAX_BYTES", "262144")), 4096), 1048576)
    if not enabled: return {"state": "DISABLED", "reason": "Logs require explicit opt-in.", "entries": []}
    if not targets: return {"state": "REFUSED", "reason": "ASSESSMENT_LOG_TARGETS is required when logs are enabled.", "entries": []}
    output, used = [], 0
    for target in targets[:20]:
        match = re.fullmatch(r"([a-z0-9]([-a-z0-9.]*[a-z0-9])?)/(pod|deployment|statefulset|daemonset)/([a-z0-9]([-a-z0-9.]*[a-z0-9])?)(?::([A-Za-z0-9._-]+))?", target)
        if not match:
            output.append({"target": target, "state": "INVALID", "reason": "Expected namespace/kind/name[:container]."}); continue
        namespace, _, kind, name, _, container = match.groups()
        command = ["kubectl", "logs", "-n", namespace, f"{kind}/{name}", "--tail=200", "--since=1h", "--timestamps=true"]
        if container: command += ["-c", container]
        result = subprocess.run(command, text=True, capture_output=True, timeout=30, check=False)
        remaining = max(0, limit - used)
        source, source_truncated = utf8_prefix(result.stdout, remaining)
        text, redaction_truncated = utf8_prefix(redact_log_text(source), remaining)
        used += len(text.encode("utf-8"))
        output.append({"target": target, "state": "COLLECTED" if result.returncode == 0 else "UNAVAILABLE", "content": text, "truncated": source_truncated or redaction_truncated, "error": redact_log_text(result.stderr[:500]) if result.returncode else ""})
        if used >= limit: break
    return {"state": "COLLECTED" if any(x.get("state") == "COLLECTED" for x in output) else "UNAVAILABLE", "maxBytes": limit, "bytes": used, "redaction": ["key-value credentials", "authorization schemes", "JWT", "AWS access keys", "URL credentials"], "entries": output}


def generate(directory: Path, workloads: list[dict[str, Any]], findings: list[dict[str, Any]], capacity: list[dict[str, Any]], technologies: list[dict[str, Any]], aws: dict[str, Any], cloud: dict[str, Any] | None = None) -> dict[str, Any]:
    nodes = items(load(directory / "nodes.json", {"items": []}))
    pods = items(load(directory / "pods.json", {"items": []}))
    events = items(load(directory / "events.json", {"items": []}))
    node_metrics = items(load(directory / "node-metrics.json", {"items": []}))
    pod_metrics = items(load(directory / "pod-metrics.json", {"items": []}))
    cloud = cloud or load(directory / "cloud-provider-assessment.json", {})
    detected = str(cloud.get("provider") or platform(nodes, aws))
    value = {"schemaVersion": "1.2", "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(), "readOnly": True, "platform": detected, "diagnostics": diagnostics(events, pods), "nodeHealth": node_health(nodes, pods, node_metrics, pod_metrics), "versions": versions(directory, nodes, workloads, technologies, detected, cloud), "manifestQuality": manifest_quality(workloads, findings), "containerTuning": tuning(capacity), "bestPractices": best_practices(detected, findings, nodes, cloud), "logs": sanitized_logs(), "cloudProvider": {"provider": detected, "state": cloud.get("state", "N/A"), "summary": cloud.get("summary") or {}, "lifecycle": cloud.get("lifecycle") or {}}}
    (directory / "operational-insights.json").write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return value
