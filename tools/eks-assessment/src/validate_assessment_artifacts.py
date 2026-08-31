#!/usr/bin/env python3
"""Smoke-test an assessment collection without printing sensitive values."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


SAFE_RUNTIME_ENV = {
    "JAVA_TOOL_OPTIONS", "JAVA_OPTS", "JDK_JAVA_OPTIONS", "CATALINA_OPTS",
    "DOTNET_GCHeapHardLimit", "DOTNET_GCHeapHardLimitPercent",
    "DOTNET_GCConserveMemory", "DOTNET_GCServer", "DOTNET_EnableDiagnostics",
    "COMPlus_GCHeapHardLimit", "COMPlus_GCHeapHardLimitPercent",
    "COMPlus_GCConserveMemory", "COMPlus_gcServer",
    "MALLOC_ARENA_MAX", "GOMEMLIMIT", "GOMAXPROCS", "NODE_OPTIONS",
    "NGINX_ENTRYPOINT_QUIET_LOGS", "KAFKA_HEAP_OPTS", "KAFKA_JVM_PERFORMANCE_OPTS",
    "RABBITMQ_SERVER_ADDITIONAL_ERL_ARGS", "RABBITMQ_VM_MEMORY_HIGH_WATERMARK",
}

SAFE_RUNTIME_ENV_UPPER = {name.upper() for name in SAFE_RUNTIME_ENV}
SAFE_RUNTIME_ENV_PREFIXES = ("DOTNET_", "COMPLUS_", "CORECLR_", "MONO_", "ASPNETCORE_")
SENSITIVE = re.compile(
    r"(?i)(password|passwd|token|secret|credential|private.?key|api.?key|"
    r"client.?secret|connection.?string)"
)
LOG_KEY_VALUE = re.compile(r'''(?ix)\b(authorization|password|passwd|token|secret|api[_-]?key|cookie|client[_-]?secret|connection[_-]?string)\b(["']?\s*[:=]\s*["']?)([^\s,;}"']+)''')
LOG_AUTH = re.compile(r"(?i)\b(?:bearer|basic)\s+[a-z0-9._~+/=-]+")
LOG_JWT = re.compile(r"\beyJ[a-zA-Z0-9_-]{8,}\.[a-zA-Z0-9_-]{8,}(?:\.[a-zA-Z0-9_-]{8,})?\b")
LOG_AWS_KEY = re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")
LOG_URL_CREDENTIALS = re.compile(r"(?i)https?://[^\s/@:]+:[^\s/@]+@")


def safe_runtime_env_name(name: str) -> bool:
    upper = name.upper()
    return upper in SAFE_RUNTIME_ENV_UPPER or upper.startswith(SAFE_RUNTIME_ENV_PREFIXES)

REQUIRED = (
    "metadata.json", "nodes.json", "pods.json", "workloads.json",
    "comprehensive-assessment.json", "application-manifests-sanitized.json",
    "api-resources.json", "universal-inventory.json", "aws-eks-assessment.json",
    "cloud-provider-assessment.json", "operational-insights.json",
)


def parse(path: Path, errors: list[str]) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        errors.append(f"invalid JSON: {path.name}: {error}")
        return {}


def walk(value: Any, filename: str, errors: list[str]) -> None:
    if isinstance(value, list):
        for child in value:
            walk(child, filename, errors)
        return
    if not isinstance(value, dict):
        return
    env = value.get("env")
    if isinstance(env, list):
        for entry in env:
            if not isinstance(entry, dict) or "value" not in entry:
                continue
            name = str(entry.get("name", ""))
            raw_value = str(entry.get("value", ""))
            safe = (
                safe_runtime_env_name(name)
                and not SENSITIVE.search(name)
                and not SENSITIVE.search(raw_value)
                and len(raw_value) <= 603
            )
            if raw_value != "<redacted>" and not safe:
                errors.append(f"unredacted env value in {filename}")
    for key, child in value.items():
        if key in {"data", "stringData", "binaryData"}:
            errors.append(f"literal config/secret data in {filename}")
        walk(child, filename, errors)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("collection", type=Path)
    args = parser.parse_args()
    root = args.collection.resolve()
    errors: list[str] = []
    documents: dict[str, Any] = {}
    if not root.is_dir():
        print(json.dumps({"ok": False, "errors": ["collection directory not found"]}))
        return 2
    for filename in REQUIRED:
        path = root / filename
        if not path.is_file():
            errors.append(f"missing artifact: {filename}")
            continue
        documents[filename] = parse(path, errors)
    for filename in ("nodes.json", "pods.json", "node-metrics.json", "pod-metrics.json", "workloads.json", "namespaces.json", "pvcs.json"):
        path = root / filename
        if path.is_file():
            walk(parse(path, errors), filename, errors)
    events = root / "events.json"
    if events.is_file():
        event_doc = parse(events, errors)
        for item in event_doc.get("items", []) if isinstance(event_doc, dict) else []:
            if isinstance(item, dict) and ({"message", "note"} & set(item)):
                errors.append("free-form event message persisted")
    secrets = root / "secrets-metadata.json"
    if secrets.is_file():
        errors.append("secret metadata artifact must not be produced")
    report = documents.get("comprehensive-assessment.json", {})
    if report.get("readOnly") is not True or (report.get("safety") or {}).get("mutations") != 0:
        errors.append("read-only safety invariant missing")
    if str(report.get("schemaVersion", "")).split(".", 1)[0] not in {"4"}:
        errors.append("unexpected comprehensive assessment schema")
    allowed_severities = {"CRIT", "WARN", "UNKNOWN", "PARTIAL", "INFO", "PASS", "N/A"}
    for finding in report.get("findings", []) if isinstance(report, dict) else []:
        if finding.get("severity") not in allowed_severities:
            errors.append("unsupported finding severity")
        if not finding.get("fingerprint") or not finding.get("ruleId") or not finding.get("resourceKey"):
            errors.append("finding lacks stable identity fields")
    quality = report.get("quality") or {}
    if any(int(quality.get(key) or 0) for key in ("stableIdentityDuplicates", "conflictingSeverities", "lowConfidencePasses")):
        errors.append("finding quality gate detected duplicate, conflicting or low-confidence PASS evidence")
    aws_report = documents.get("aws-eks-assessment.json", {})
    if aws_report.get("readOnly") is not True or (aws_report.get("safety") or {}).get("mutations") != 0:
        errors.append("AWS/EKS read-only safety invariant missing")
    cis_path = root / "cis-security-assessment.json"
    if cis_path.is_file():
        cis_report = parse(cis_path, errors)
        modern_cis = str(cis_report.get("schemaVersion", "1.0")) >= "1.1"
        allowed_applicability = {"APPLICABLE", "NOT_APPLICABLE", "MANAGED_PROVIDER", "EVIDENCE_UNAVAILABLE", "MANUAL_REVIEW"}
        allowed_responsibility = {"CUSTOMER", "CLOUD_PROVIDER", "SHARED"}
        if cis_report.get("readOnly") is not True or "Não representa certificação" not in str(cis_report.get("notice", "")):
            errors.append("CIS posture disclaimer or read-only invariant missing")
        for control in cis_report.get("controls", []):
            if control.get("applicability") not in allowed_applicability or control.get("managedResponsibility") not in allowed_responsibility:
                errors.append("invalid CIS applicability or responsibility")
            if control.get("applicability") in {"EVIDENCE_UNAVAILABLE", "MANUAL_REVIEW"} and control.get("status") == "PASS":
                errors.append("CIS control passed without evidence")
            if modern_cis and (not control.get("domain") or control.get("riskWeight") not in {1, 2, 3} or not control.get("validationCommand")):
                errors.append("CIS control lacks prioritization metadata")
        cis_summary = cis_report.get("summary") or {}
        if modern_cis and (cis_summary.get("postureScorePercent") is None or cis_summary.get("evidenceCoveragePercent") is None or not isinstance(cis_summary.get("domains"), list)):
            errors.append("CIS posture, evidence coverage or domain scores missing")
    operational = documents.get("operational-insights.json", {})
    required_domains = {"diagnostics", "nodeHealth", "versions", "manifestQuality", "containerTuning", "bestPractices", "logs"}
    if operational.get("readOnly") is not True or not required_domains.issubset(operational):
        errors.append("operational insights domains or read-only invariant missing")
    node_health = operational.get("nodeHealth") or {}
    if node_health.get("state") not in {"PASS", "WARN", "CRIT", "PARTIAL", "EVIDENCE_UNAVAILABLE"}:
        errors.append("invalid Node Health state")
    for item in node_health.get("items") or []:
        evidence = item.get("evidence") or {}
        if item.get("state") == "PASS" and (not item.get("ready") or evidence.get("metrics") != "MetricsAPI"):
            errors.append("Node Health passed without Ready and Metrics API evidence")
    log_evidence = operational.get("logs") or {}
    if log_evidence.get("state") == "COLLECTED" and not log_evidence.get("redaction"):
        errors.append("collected logs lack redaction metadata")
    for entry in log_evidence.get("entries") or []:
        content = str(entry.get("content") or "") + "\n" + str(entry.get("error") or "")
        key_value_leak = any("[REDACTED" not in match.group(3).upper() for match in LOG_KEY_VALUE.finditer(content))
        if key_value_leak or LOG_AUTH.search(content) or LOG_JWT.search(content) or LOG_AWS_KEY.search(content) or LOG_URL_CREDENTIALS.search(content):
            errors.append("unredacted credential pattern in optional logs")
    cloud = documents.get("cloud-provider-assessment.json", {})
    if cloud.get("readOnly") is not True or (cloud.get("safety") or {}).get("mutations") != 0:
        errors.append("cloud-provider read-only safety invariant missing")
    if cloud.get("provider") not in {"eks", "aks", "gke", "generic-kubernetes"}:
        errors.append("unsupported cloud provider")
    if (cloud.get("safety") or {}).get("rawPayloadsPersisted") is not False:
        errors.append("cloud-provider raw payload persistence invariant missing")
    allowed_cloud_operations = {"get", "list", "describe", "show"}
    if not set((cloud.get("safety") or {}).get("operations") or []).issubset(allowed_cloud_operations):
        errors.append("cloud-provider report declares a mutable operation")
    forbidden_cloud_keys = {"accountId", "subscriptionId", "tenantId", "projectId", "resourceId", "fqdn", "endpoint"}
    def cloud_keys(value: Any) -> set[str]:
        if isinstance(value, dict):
            return set(value).intersection(forbidden_cloud_keys) | set().union(*(cloud_keys(child) for child in value.values()))
        if isinstance(value, list):
            return set().union(*(cloud_keys(child) for child in value)) if value else set()
        return set()
    if cloud_keys(cloud):
        errors.append("cloud-provider report contains a forbidden account or endpoint identifier")
    if cloud.get("state") not in {"AVAILABLE", "PARTIAL"} and any(item.get("status") == "PASS" for item in cloud.get("bestPractices") or []):
        errors.append("cloud-provider best practice passed without provider evidence")
    summary = report.get("summary") or {}
    if int(summary.get("workloads") or 0) == 0 or int(summary.get("containers") or 0) == 0:
        errors.append("workload/container inventory is empty")
    universal = documents.get("universal-inventory.json", {})
    if int(universal.get("resourceTypes") or 0) == 0:
        errors.append("universal API inventory is empty")
    allowed_identity_keys = {"apiVersion", "kind", "namespace", "name"}
    for entry in universal.get("resources", []) if isinstance(universal, dict) else []:
        for identity in entry.get("objects", []) if isinstance(entry, dict) else []:
            if isinstance(identity, dict) and not set(identity).issubset(allowed_identity_keys):
                errors.append("universal inventory contains fields beyond safe identity")
    inventory = {
        "nodes": len((documents.get("nodes.json") or {}).get("items", [])),
        "pods": len((documents.get("pods.json") or {}).get("items", [])),
        "workloadObjects": len((documents.get("workloads.json") or {}).get("items", [])),
        "assessedWorkloads": summary.get("workloads", 0),
        "containers": summary.get("containers", 0),
        "checks": summary.get("checks", 0),
        "capacityRecommendations": summary.get("capacityRecommendations", 0),
        "apiResourceTypes": universal.get("resourceTypes", 0),
        "objectsInventoried": universal.get("objectCount", 0),
        "awsEksState": (documents.get("aws-eks-assessment.json") or {}).get("state", "UNKNOWN"),
        "unknown": summary.get("unknown", 0),
        "partial": summary.get("partial", 0),
    }
    unique_errors = list(dict.fromkeys(errors))
    print(json.dumps({"ok": not unique_errors, "inventory": inventory, "errors": unique_errors}, ensure_ascii=False))
    return 1 if unique_errors else 0


if __name__ == "__main__":
    sys.exit(main())
