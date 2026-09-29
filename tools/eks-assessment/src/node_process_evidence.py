#!/usr/bin/env python3
"""Optional provider-neutral node usage attribution from Prometheus evidence."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

from prometheus_telemetry import TelemetryError, get_json, validate_url


QUERIES = {
    "hostCpuCores": 'sum by (instance, node) (rate(node_cpu_seconds_total{mode!="idle",mode!="iowait"}[5m]))',
    "containerCpuCores": 'sum by (instance, node) (rate(container_cpu_usage_seconds_total{container!="",image!=""}[5m]))',
    "runtimeCpuCores": 'sum by (instance, node, job) (rate(process_cpu_seconds_total{job=~".*(kubelet|containerd|cri-o).*"}[5m]))',
    "hostMemoryBytes": 'max by (instance, node) (node_memory_MemTotal_bytes - node_memory_MemAvailable_bytes)',
    "containerMemoryBytes": 'sum by (instance, node) (container_memory_working_set_bytes{container!="",image!=""})',
    "runtimeMemoryBytes": 'sum by (instance, node, job) (process_resident_memory_bytes{job=~".*(kubelet|containerd|cri-o).*"})',
}


def utc_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def load(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def aliases(nodes_file: Path) -> dict[str, str]:
    document = load(nodes_file, {})
    result: dict[str, str] = {}
    for node in document.get("items") or []:
        if not isinstance(node, dict):
            continue
        name = str((node.get("metadata") or {}).get("name") or "")
        if not name:
            continue
        result[name.lower()] = name
        for address in (node.get("status") or {}).get("addresses") or []:
            value = str(address.get("address") or "").lower()
            if value:
                result[value] = name
    return result


def instance_host(value: str) -> str:
    if value.startswith("[") and "]" in value:
        return value[1:value.index("]")].lower()
    return value.rsplit(":", 1)[0].lower() if value.count(":") == 1 else value.lower()


def query_values(base_url: str, query: str) -> list[tuple[dict[str, str], float]]:
    response = get_json(base_url, "/api/v1/query", {"query": query})
    if response.get("status") != "success":
        raise TelemetryError("Prometheus query did not return success")
    output: list[tuple[dict[str, str], float]] = []
    for series in (response.get("data") or {}).get("result") or []:
        try:
            value = float((series.get("value") or [None, None])[1])
        except (TypeError, ValueError, IndexError):
            continue
        if not math.isfinite(value) or value < 0:
            continue
        labels = {str(key): str(item) for key, item in (series.get("metric") or {}).items()}
        output.append((labels, value))
    return output


def collect(url: str, nodes_file: Path) -> dict[str, Any]:
    base_url = validate_url(url)
    node_aliases = aliases(nodes_file)
    values: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    errors: dict[str, str] = {}
    unmatched: set[str] = set()
    available_queries = 0
    for metric, query in QUERIES.items():
        try:
            rows = query_values(base_url, query)
            available_queries += bool(rows)
        except TelemetryError as error:
            errors[metric] = str(error)[:300]
            continue
        for labels, value in rows:
            raw = labels.get("node") or labels.get("kubernetes_node") or labels.get("instance") or ""
            host = instance_host(raw)
            node = node_aliases.get(host) or node_aliases.get(raw.lower())
            if not node:
                unmatched.add(raw or "unlabeled")
                continue
            values[node][metric] += value
    items = []
    for node in sorted(set(node_aliases.values())):
        row = values.get(node, {})
        host_cpu = row.get("hostCpuCores")
        container_cpu = row.get("containerCpuCores")
        runtime_cpu = row.get("runtimeCpuCores")
        host_memory = row.get("hostMemoryBytes")
        container_memory = row.get("containerMemoryBytes")
        runtime_memory = row.get("runtimeMemoryBytes")

        def breakdown(total: float | None, containers: float | None, runtime: float | None) -> dict[str, Any]:
            if total is None:
                return {"total": None, "kubernetesContainers": containers, "kubeletAndRuntime": runtime, "operatingSystemUnattributed": None}
            return {
                "total": total,
                "kubernetesContainers": containers,
                "kubeletAndRuntime": runtime,
                "operatingSystemUnattributed": max(0.0, total - (containers or 0.0) - (runtime or 0.0)),
            }

        evidence_count = sum(value is not None for value in (host_cpu, container_cpu, runtime_cpu, host_memory, container_memory, runtime_memory))
        state = "AVAILABLE" if host_cpu is not None and host_memory is not None and evidence_count >= 4 else "PARTIAL" if evidence_count else "EVIDENCE_UNAVAILABLE"
        items.append({
            "node": node,
            "state": state,
            "cpuCores": breakdown(host_cpu, container_cpu, runtime_cpu),
            "memoryBytes": breakdown(host_memory, container_memory, runtime_memory),
            "source": "Prometheus",
        })
    states = [item["state"] for item in items]
    overall = "AVAILABLE" if states and all(item == "AVAILABLE" for item in states) else "PARTIAL" if any(item != "EVIDENCE_UNAVAILABLE" for item in states) else "EVIDENCE_UNAVAILABLE"
    return {
        "schemaVersion": "1.0",
        "generatedAt": utc_iso(),
        "readOnly": True,
        "state": overall,
        "summary": {
            "nodes": len(items),
            "available": sum(item == "AVAILABLE" for item in states),
            "partial": sum(item == "PARTIAL" for item in states),
            "evidenceUnavailable": sum(item == "EVIDENCE_UNAVAILABLE" for item in states),
            "queriesWithData": available_queries,
            "queries": len(QUERIES),
        },
        "items": items,
        "unmatchedInstances": sorted(unmatched)[:50],
        "queryErrors": errors,
        "notice": "Atribuição opcional baseada em métricas Prometheus. operatingSystemUnattributed é residual estimado; não equivale a profiling de processos e pode conter lacunas ou sobreposição de séries.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Coleta atribuição avançada e read-only de uso dos nodes")
    parser.add_argument("--url", required=True)
    parser.add_argument("--nodes-file", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        report = collect(args.url, args.nodes_file)
    except TelemetryError as error:
        report = {
            "schemaVersion": "1.0", "generatedAt": utc_iso(), "readOnly": True,
            "state": "EVIDENCE_UNAVAILABLE", "summary": {"nodes": 0, "available": 0, "partial": 0, "evidenceUnavailable": 0, "queriesWithData": 0, "queries": len(QUERIES)},
            "items": [], "unmatchedInstances": [], "queryErrors": {"connection": str(error)[:300]},
            "notice": "Evidência avançada de node indisponível; nenhuma conformidade foi inferida.",
        }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": report["state"] != "EVIDENCE_UNAVAILABLE", "state": report["state"], **report["summary"]}, ensure_ascii=False))
    return 0 if report["state"] in {"AVAILABLE", "PARTIAL"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
