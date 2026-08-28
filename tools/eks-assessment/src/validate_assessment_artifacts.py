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


def safe_runtime_env_name(name: str) -> bool:
    upper = name.upper()
    return upper in SAFE_RUNTIME_ENV_UPPER or upper.startswith(SAFE_RUNTIME_ENV_PREFIXES)

REQUIRED = (
    "metadata.json", "nodes.json", "pods.json", "workloads.json",
    "comprehensive-assessment.json", "application-manifests-sanitized.json",
    "api-resources.json", "universal-inventory.json", "aws-eks-assessment.json",
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
    for filename in ("nodes.json", "pods.json", "workloads.json", "namespaces.json", "pvcs.json"):
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
    aws_report = documents.get("aws-eks-assessment.json", {})
    if aws_report.get("readOnly") is not True or (aws_report.get("safety") or {}).get("mutations") != 0:
        errors.append("AWS/EKS read-only safety invariant missing")
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
