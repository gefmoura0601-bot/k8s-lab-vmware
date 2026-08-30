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


def versions(nodes: list[dict[str, Any]], workloads: list[dict[str, Any]], technologies: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    node_versions = Counter()
    for node in nodes:
        meta, info = node.get("metadata") or {}, (node.get("status") or {}).get("nodeInfo") or {}
        row = {"component": "Node", "name": meta.get("name", "-"), "version": info.get("kubeletVersion", "UNKNOWN"), "runtime": info.get("containerRuntimeVersion", "UNKNOWN"), "os": info.get("osImage", "UNKNOWN"), "kernel": info.get("kernelVersion", "UNKNOWN"), "state": "DETECTED", "source": "KubernetesAPI"}
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
        rows.append({"component": "Container image", "name": name, "version": ", ".join(sorted(detected)), "runtime": "-", "os": "-", "kernel": "-", "state": "UNKNOWN" if "UNKNOWN" in detected else "DETECTED", "source": "KubernetesAPI"})
    for technology in technologies:
        if technology.get("state") == "DETECTED" and not any(x["name"].lower() == str(technology.get("name", "")).lower() for x in rows):
            rows.append({"component": "Technology", "name": technology.get("name"), "version": "UNKNOWN", "runtime": "-", "os": "-", "kernel": "-", "state": "UNKNOWN", "source": "Heuristic"})
    skew = len(node_versions) > 1
    return {"summary": {"components": len(rows), "unknownVersions": sum(x["version"] == "UNKNOWN" for x in rows), "nodeVersionSkew": skew}, "items": rows, "notice": "Lifecycle/EOL exige catálogo oficial atualizado; UNKNOWN nunca é apresentado como suportado."}


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


def best_practices(detected: str, findings: list[dict[str, Any]], nodes: list[dict[str, Any]]) -> dict[str, Any]:
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
    for provider, rules in provider_rules.items():
        for rule_id, domain, recommendation in rules:
            applicable = provider == detected
            rows.append({"ruleId": f"bestpractice.{rule_id}", "provider": provider, "domain": domain, "status": "MANUAL" if applicable else "N/A", "applicability": "MANUAL_REVIEW" if applicable else "NOT_APPLICABLE", "responsibility": "SHARED" if applicable else "CLOUD_PROVIDER", "resource": "cluster", "evidence": "Provider detected; cloud API evidence is required." if applicable else f"Detected platform: {detected}", "recommendation": recommendation})
    return {"platform": detected, "summary": dict(Counter(x["status"] for x in rows)), "rules": rows, "notice": "Recomendações baseadas em evidência disponível; não representam certificação do provider."}


SECRET = re.compile(r"(?i)(authorization|password|passwd|token|secret|api[_-]?key|cookie)(\s*[:=]\s*)([^\s,;]+)")
BEARER = re.compile(r"(?i)bearer\s+[a-z0-9._~+/=-]+")


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
        text = result.stdout[:max(0, limit-used)]
        text = SECRET.sub(r"\1\2[REDACTED]", BEARER.sub("Bearer [REDACTED]", text))
        used += len(text.encode("utf-8"))
        output.append({"target": target, "state": "COLLECTED" if result.returncode == 0 else "UNAVAILABLE", "content": text, "truncated": len(result.stdout) > len(text), "error": result.stderr[:500] if result.returncode else ""})
        if used >= limit: break
    return {"state": "COLLECTED" if any(x.get("state") == "COLLECTED" for x in output) else "UNAVAILABLE", "maxBytes": limit, "bytes": used, "redaction": ["credentials", "bearer tokens"], "entries": output}


def generate(directory: Path, workloads: list[dict[str, Any]], findings: list[dict[str, Any]], capacity: list[dict[str, Any]], technologies: list[dict[str, Any]], aws: dict[str, Any]) -> dict[str, Any]:
    nodes = items(load(directory / "nodes.json", {"items": []}))
    pods = items(load(directory / "pods.json", {"items": []}))
    events = items(load(directory / "events.json", {"items": []}))
    detected = platform(nodes, aws)
    value = {"schemaVersion": "1.0", "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(), "readOnly": True, "platform": detected, "diagnostics": diagnostics(events, pods), "versions": versions(nodes, workloads, technologies), "manifestQuality": manifest_quality(workloads, findings), "containerTuning": tuning(capacity), "bestPractices": best_practices(detected, findings, nodes), "logs": sanitized_logs()}
    (directory / "operational-insights.json").write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return value
