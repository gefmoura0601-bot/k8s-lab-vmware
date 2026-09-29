#!/usr/bin/env python3
"""Offline structural and semantic checks for sanitized Kubernetes manifests."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from collections import Counter
from pathlib import Path
from typing import Any


DEPRECATED_APIS = {
    "extensions/v1beta1": "Use a stable API supported by the target cluster.",
    "apps/v1beta1": "Use apps/v1.",
    "apps/v1beta2": "Use apps/v1.",
    "networking.k8s.io/v1beta1": "Use networking.k8s.io/v1.",
    "policy/v1beta1": "Use policy/v1 when the resource supports it.",
    "batch/v1beta1": "Use batch/v1.",
    "autoscaling/v2beta1": "Use autoscaling/v2.",
    "autoscaling/v2beta2": "Use autoscaling/v2.",
}
RESOURCE_NAMES = {
    "ConfigMap": "configmaps",
    "CronJob": "cronjobs.batch",
    "DaemonSet": "daemonsets.apps",
    "Deployment": "deployments.apps",
    "HorizontalPodAutoscaler": "horizontalpodautoscalers.autoscaling",
    "Ingress": "ingresses.networking.k8s.io",
    "Job": "jobs.batch",
    "Namespace": "namespaces",
    "NetworkPolicy": "networkpolicies.networking.k8s.io",
    "PersistentVolumeClaim": "persistentvolumeclaims",
    "Pod": "pods",
    "PodDisruptionBudget": "poddisruptionbudgets.policy",
    "Role": "roles.rbac.authorization.k8s.io",
    "RoleBinding": "rolebindings.rbac.authorization.k8s.io",
    "Secret": "secrets",
    "Service": "services",
    "ServiceAccount": "serviceaccounts",
    "StatefulSet": "statefulsets.apps",
}
POD_TEMPLATE_KINDS = {"Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job"}


def utc_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def load(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def finding(rule_id: str, severity: str, manifest: dict[str, Any], message: str, recommendation: str) -> dict[str, Any]:
    metadata = manifest.get("metadata") if isinstance(manifest.get("metadata"), dict) else {}
    return {
        "ruleId": rule_id,
        "severity": severity,
        "namespace": metadata.get("namespace") or "cluster",
        "resource": f"{manifest.get('kind') or 'Unknown'}/{metadata.get('name') or 'unknown'}",
        "evidence": message,
        "recommendation": recommendation,
    }


def pod_spec(manifest: dict[str, Any]) -> dict[str, Any]:
    spec = manifest.get("spec") if isinstance(manifest.get("spec"), dict) else {}
    kind = manifest.get("kind")
    if kind == "Pod":
        return spec
    template = spec.get("template") if isinstance(spec.get("template"), dict) else {}
    return template.get("spec") if isinstance(template.get("spec"), dict) else {}


def expected_resource(kind: str, api_version: str) -> str:
    if kind in RESOURCE_NAMES:
        return RESOURCE_NAMES[kind]
    group = api_version.split("/", 1)[0] if "/" in api_version else ""
    plural = kind.lower() + ("es" if kind.lower().endswith(("s", "x", "ch", "sh")) else "s")
    return f"{plural}.{group}" if group else plural


def evaluate_collection(collection: Path) -> dict[str, Any]:
    source = load(collection / "application-manifests-sanitized.json", {})
    raw_items = source.get("items") if isinstance(source, dict) else []
    manifests = [item for item in raw_items or [] if isinstance(item, dict)]
    resources = load(collection / "api-resources.json", {})
    available = set(resources.get("namespaced") or []) | set(resources.get("clusterScoped") or [])
    api_state = str(resources.get("state") or "UNAVAILABLE")
    findings: list[dict[str, Any]] = []
    identities: set[tuple[str, str, str]] = set()
    checked = 0
    served_checked = 0
    for manifest in manifests:
        api_version = str(manifest.get("apiVersion") or "")
        kind = str(manifest.get("kind") or "")
        metadata = manifest.get("metadata") if isinstance(manifest.get("metadata"), dict) else {}
        name = str(metadata.get("name") or "")
        namespace = str(metadata.get("namespace") or "cluster")
        identity = (namespace, kind, name)
        if identity in identities:
            continue
        identities.add(identity)
        checked += 1
        if not api_version or not kind or not name:
            findings.append(finding("manifest.required-fields", "CRIT", manifest, "apiVersion, kind ou metadata.name ausente.", "Defina os campos de identidade obrigatórios do objeto Kubernetes."))
            continue
        if api_version in DEPRECATED_APIS:
            findings.append(finding("manifest.deprecated-api", "WARN", manifest, f"apiVersion={api_version}", DEPRECATED_APIS[api_version]))
        if api_state == "AVAILABLE":
            served_checked += 1
            resource = expected_resource(kind, api_version)
            if resource not in available:
                findings.append(finding("manifest.api-not-served", "WARN", manifest, f"Resource esperado {resource} não aparece em api-resources.", "Confirme o CRD/API e a apiVersion no cluster alvo antes do deploy."))
        spec = manifest.get("spec")
        if kind not in {"Namespace", "ConfigMap", "Secret", "ServiceAccount", "Role", "RoleBinding"} and not isinstance(spec, dict):
            findings.append(finding("manifest.spec-required", "CRIT", manifest, "spec ausente ou inválido.", "Defina um spec compatível com o kind."))
            continue
        if kind in {"Deployment", "StatefulSet", "DaemonSet", "ReplicaSet"}:
            selector = ((spec or {}).get("selector") or {}).get("matchLabels") or {}
            labels = ((((spec or {}).get("template") or {}).get("metadata") or {}).get("labels") or {})
            if not selector or any(labels.get(key) != value for key, value in selector.items()):
                findings.append(finding("manifest.selector-labels", "CRIT", manifest, "spec.selector.matchLabels não corresponde aos labels do Pod template.", "Alinhe selector e template labels; selectors de workloads são imutáveis após criação."))
        if kind == "Service":
            selector = (spec or {}).get("selector") or {}
            if not selector and (spec or {}).get("type") != "ExternalName":
                findings.append(finding("manifest.service-selector", "WARN", manifest, "Service sem selector e sem type ExternalName.", "Confirme se o Service headless/manual é intencional ou defina o selector."))
        if kind in POD_TEMPLATE_KINDS | {"Pod", "CronJob"}:
            effective_spec = pod_spec(manifest)
            if kind == "CronJob":
                effective_spec = (((spec or {}).get("jobTemplate") or {}).get("spec") or {}).get("template", {}).get("spec", {})
            containers = effective_spec.get("containers") if isinstance(effective_spec, dict) else None
            if not isinstance(containers, list) or not containers:
                findings.append(finding("manifest.containers", "CRIT", manifest, "Nenhum container principal foi definido.", "Defina ao menos um item em spec.containers."))
            else:
                names: set[str] = set()
                for container in containers:
                    if not isinstance(container, dict):
                        continue
                    container_name = str(container.get("name") or "")
                    if not container_name or container_name in names:
                        findings.append(finding("manifest.container-name", "CRIT", manifest, "Container sem nome ou com nome duplicado.", "Use nomes DNS-label únicos no Pod."))
                    names.add(container_name)
                    if not str(container.get("image") or "").strip():
                        findings.append(finding("manifest.container-image", "CRIT", manifest, f"Container {container_name or 'unknown'} sem image.", "Defina uma imagem versionada ou por digest."))
    counts = Counter(item["severity"] for item in findings)
    state = "FAIL" if counts["CRIT"] else "WARN" if counts["WARN"] else "PARTIAL"
    report = {
        "schemaVersion": "1.0",
        "generatedAt": utc_iso(),
        "readOnly": True,
        "assessmentMode": "OFFLINE_SEMANTIC",
        "state": state,
        "summary": {
            "manifests": checked,
            "critical": counts["CRIT"],
            "warnings": counts["WARN"],
            "apiResourcesState": api_state,
            "apiServedChecks": served_checked,
            "openApiSchema": "EVIDENCE_UNAVAILABLE",
        },
        "findings": findings,
        "notice": "Validação offline estrutural e semântica sobre manifests sanitizados. Não executa server-side dry-run e não substitui validação OpenAPI/admission no pipeline do cluster alvo.",
    }
    (collection / "manifest-schema-validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Validação offline de manifests Kubernetes sanitizados")
    parser.add_argument("--collection", required=True, type=Path)
    args = parser.parse_args()
    if not args.collection.is_dir():
        parser.error("collection not found")
    report = evaluate_collection(args.collection)
    print(json.dumps({"ok": report["state"] != "FAIL", "state": report["state"], **report["summary"]}, ensure_ascii=False))
    return 1 if report["state"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
