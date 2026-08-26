#!/usr/bin/env python3
"""Read-only Prometheus assessment with automatic workload/runtime metric discovery."""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

WINDOWS = {"1d": 300, "3d": 600, "7d": 900, "14d": 1800, "30d": 3600}
MAX_ROLE_CANDIDATES = 6
METRIC_NAME = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_:]*$")
SENSITIVE = re.compile(
    r"(?i)(password|passwd|token|secret|credential|private.?key|api.?key|"
    r"client.?secret|connection.?string)"
)
SAFE_RUNTIME_ENV = {
    "JAVA_OPTS", "JAVA_TOOL_OPTIONS", "JDK_JAVA_OPTIONS", "CATALINA_OPTS",
    "KAFKA_HEAP_OPTS", "KAFKA_JVM_PERFORMANCE_OPTS",
    "DOTNET_GCHeapHardLimit", "DOTNET_GCHeapHardLimitPercent",
    "DOTNET_GCConserveMemory", "DOTNET_gcServer", "COMPlus_gcServer",
    "COMPlus_GCHeapHardLimit", "COMPlus_GCHeapHardLimitPercent",
    "DOTNET_EnableDiagnostics", "ASPNETCORE_ENVIRONMENT", "MALLOC_ARENA_MAX",
}
SAFE_RUNTIME_ENV_UPPER = {name.upper() for name in SAFE_RUNTIME_ENV}
SAFE_RUNTIME_PREFIXES = ("DOTNET_", "COMPLUS_", "CORECLR_", "MONO_", "ASPNETCORE_")
RUNTIME_DETECTION = {
    "JVM": re.compile(
        r"(?i)(\bjava\b|openjdk|temurin|corretto|\.jar\b|spring|quarkus|"
        r"wildfly|jboss|tomcat|JAVA_TOOL_OPTIONS|JAVA_OPTS|JDK_JAVA_OPTIONS|"
        r"KAFKA_HEAP_OPTS)"
    ),
    ".NET": re.compile(
        r"(?i)(\bdotnet\b|aspnet|\.dll\b|mcr\.microsoft\.com/dotnet|"
        r"DOTNET_|COMPlus_|CORECLR_|MONO_)"
    ),
}

CORE_RULES = {
    "cpu": (
        r"^container_cpu_usage_seconds_total$",
        r"container.*cpu.*usage.*seconds.*total$",
    ),
    "memory": (
        r"^container_memory_usage_bytes$",
        r"container.*memory.*usage.*bytes$",
    ),
    "memory_working_set": (
        r"^container_memory_working_set_bytes$",
        r"container.*memory.*working.*set.*bytes$",
    ),
    "cpu_throttled": (
        r"^container_cpu_cfs_throttled_periods_total$",
        r"container.*cpu.*throttled.*periods.*total$",
    ),
    "cpu_periods": (
        r"^container_cpu_cfs_periods_total$",
        r"container.*cpu.*periods.*total$",
    ),
}

RUNTIME_RULES = {
    "JVM": {
        "info": (
            r"^jvm_info$",
            r"(?:^|_)jvm(?:_|.*).*info$",
            r"process_runtime_jvm.*info$",
        ),
        "heap_used": (
            r"^jvm_memory_used_bytes$",
            r"(?:^|_)jvm(?:_|.*).*memory.*(?:used|usage).*bytes$",
            r"process_runtime_jvm.*memory.*(?:used|usage).*bytes$",
        ),
        "heap_max": (
            r"^jvm_memory_max_bytes$",
            r"(?:^|_)jvm(?:_|.*).*memory.*(?:max|limit).*bytes$",
            r"process_runtime_jvm.*memory.*(?:max|limit).*bytes$",
        ),
        "threads": (
            r"^jvm_threads_live_threads$",
            r"(?:^|_)jvm(?:_|.*).*threads?.*(?:live|current|count)$",
            r"process_runtime_jvm.*thread.*count$",
        ),
        "gc": (
            r"^jvm_gc_pause_seconds_sum$",
            r"^jvm_gc_overhead$",
            r"(?:^|_)jvm(?:_|.*).*gc.*(?:pause|overhead|duration).*(?:sum|ratio|seconds)?$",
            r"process_runtime_jvm.*gc.*(?:pause|duration|time).*$",
        ),
        "allocation": (
            r"^jvm_gc_memory_allocated_bytes_total$",
            r"(?:^|_)jvm(?:_|.*).*allocat.*bytes.*(?:total|count)$",
            r"process_runtime_jvm.*allocat.*bytes.*(?:total|count)$",
        ),
        "native_memory": (
            r"^jvm_native_memory_committed_bytes$",
            r"(?:^|_)jvm(?:_|.*).*native.*memory.*(?:committed|used).*bytes$",
        ),
    },
    ".NET": {
        "info": (
            r"^dotnet_build_info$",
            r"(?:^|_)(?:dotnet|clr)(?:_|.*).*(?:build_)?info$",
            r"process_runtime_dotnet.*info$",
        ),
        "heap_used": (
            r"^dotnet_gc_heap_size_bytes$",
            r"^dotnet_total_memory_bytes$",
            r"(?:^|_)(?:dotnet|clr)(?:_|.*).*gc.*heap.*(?:size|used|usage).*bytes$",
            r"process_runtime_dotnet.*gc.*heap.*(?:size|used|usage).*bytes$",
        ),
        "heap_max": (
            r"^dotnet_gc_memory_total_available_bytes$",
            r"(?:^|_)(?:dotnet|clr)(?:_|.*).*gc.*(?:available|hard.?limit|max).*bytes$",
            r"process_runtime_dotnet.*gc.*(?:available|limit|max).*bytes$",
        ),
        "threads": (
            r"^dotnet_threadpool_num_threads$",
            r"(?:^|_)(?:dotnet|clr)(?:_|.*).*thread.*(?:num_threads|thread_count|current)$",
            r"process_runtime_dotnet.*thread.*count$",
        ),
        "gc": (
            r"^dotnet_gc_pause_ratio$",
            r"^dotnet_gc_collection_seconds_sum$",
            r"(?:^|_)(?:dotnet|clr)(?:_|.*).*gc.*(?:pause|collection|duration).*(?:ratio|seconds_sum|time_total)$",
            r"process_runtime_dotnet.*gc.*(?:pause|duration|time).*$",
        ),
        "allocation": (
            r"^dotnet_gc_allocated_bytes_total$",
            r"(?:^|_)(?:dotnet|clr)(?:_|.*).*allocat.*bytes.*(?:total|count)$",
            r"process_runtime_dotnet.*allocat.*bytes.*(?:total|count)$",
        ),
        "exceptions": (
            r"^dotnet_exceptions_total$",
            r"(?:^|_)(?:dotnet|clr)(?:_|.*).*exception.*(?:total|count)$",
            r"process_runtime_dotnet.*exception.*(?:total|count)$",
        ),
        "working_set": (
            r"^process_working_set_bytes$",
            r"^process_resident_memory_bytes$",
            r"process.*(?:working_set|resident_memory).*bytes$",
        ),
    },
}
ROLE_UNITS = {
    "heap_used": "bytes",
    "heap_max": "bytes",
    "threads": "count",
    "gc": "percent",
    "allocation": "bytes_per_second",
    "native_memory": "bytes",
    "exceptions": "per_second",
    "working_set": "bytes",
}
ROLE_OUTPUT = {
    "heap_used": "heap_used",
    "heap_max": "heap_max",
    "threads": "threads",
    "gc": "gc",
    "allocation": "allocation",
    "native_memory": "native_memory",
    "exceptions": "exceptions",
    "working_set": "working_set",
}
RUNTIME_PREFIX = {"JVM": "jvm", ".NET": "dotnet"}
RUNTIME_METADATA_KEYS = {
    "runtime", "vendor", "version", "runtime_version", "target_framework",
    "gc_mode", "process_architecture", "os_version",
}
STANDARD_FALLBACK_METRICS = {
    "container_cpu_usage_seconds_total",
    "container_memory_usage_bytes",
    "container_memory_working_set_bytes",
    "container_cpu_cfs_throttled_periods_total",
    "container_cpu_cfs_periods_total",
    "jvm_info",
    "jvm_memory_used_bytes",
    "jvm_memory_max_bytes",
    "jvm_threads_live_threads",
    "jvm_gc_pause_seconds_sum",
    "jvm_gc_memory_allocated_bytes_total",
    "dotnet_build_info",
    "dotnet_gc_heap_size_bytes",
    "dotnet_gc_memory_total_available_bytes",
    "dotnet_threadpool_num_threads",
    "dotnet_gc_pause_ratio",
    "dotnet_gc_collection_seconds_sum",
    "dotnet_gc_allocated_bytes_total",
    "dotnet_exceptions_total",
    "process_working_set_bytes",
    "process_resident_memory_bytes",
}


class TelemetryError(Exception):
    pass


@dataclass(frozen=True)
class WorkloadTarget:
    namespace: str
    deployment: str
    runtime_hints: tuple[str, ...] = ()
    runtime_config: tuple[tuple[str, str, str], ...] = ()


@dataclass
class SelectorMatch:
    selector: str
    labels: tuple[str, ...]
    matched_by: str
    series_labels: dict[str, str]


@dataclass
class MetricBinding:
    key: str
    query: str
    source_metric: str
    runtime: str = ""
    unit: str = ""
    selector_labels: tuple[str, ...] = ()


@dataclass
class MetricResult:
    state: str
    metric: str
    query: str
    source_metric: str = ""
    runtime: str = ""
    unit: str = ""
    selector_labels: tuple[str, ...] = ()
    mean: float | None = None
    peak: float | None = None
    p50: float | None = None
    p90: float | None = None
    p95: float | None = None
    p99: float | None = None
    samples: int = 0
    reason: str | None = None


def validate_url(value: str) -> str:
    if not value:
        raise TelemetryError("PROMETHEUS_URL is not configured")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise TelemetryError("Prometheus URL must use HTTP or HTTPS")
    if parsed.username or parsed.password:
        raise TelemetryError("Prometheus URL must not include credentials")
    if parsed.query or parsed.fragment:
        raise TelemetryError("Prometheus URL must not include query or fragment")
    return value.rstrip("/")


def get_json(
    base_url: str,
    path: str,
    params: dict[str, Any] | None = None,
    timeout: int = 15,
) -> dict[str, Any]:
    query = urlencode(params or {}, doseq=True)
    request = Request(
        f"{base_url}{path}{'?' + query if query else ''}",
        method="GET",
        headers={"Accept": "application/json", "User-Agent": "eks-prometheus-assessment/2.0"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                raise TelemetryError(f"Prometheus returned HTTP {response.status}")
            return json.loads(response.read().decode("utf-8"))
    except TelemetryError:
        raise
    except Exception as error:
        raise TelemetryError(f"Prometheus endpoint unavailable: {error}") from error


def percentile(values: list[float], ratio: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * ratio
    low, high = math.floor(position), math.ceil(position)
    return (
        ordered[low]
        if low == high
        else ordered[low] + (ordered[high] - ordered[low]) * (position - low)
    )


def values_from_response(response: dict[str, Any]) -> list[float]:
    total_by_timestamp: dict[float, float] = {}
    for series in response.get("data", {}).get("result", []):
        for timestamp, value in series.get("values", []):
            try:
                point = float(value)
                if not math.isfinite(point):
                    continue
                stamp = float(timestamp)
                total_by_timestamp[stamp] = total_by_timestamp.get(stamp, 0.0) + point
            except (TypeError, ValueError):
                continue
    return list(total_by_timestamp.values())


def no_data(
    key: str,
    reason: str,
    source_metric: str = "",
    runtime: str = "",
    unit: str = "",
) -> MetricResult:
    return MetricResult(
        "NO_DATA",
        key,
        "",
        source_metric=source_metric,
        runtime=runtime,
        unit=unit,
        reason=reason,
    )


def collect_metric(
    base_url: str,
    binding: MetricBinding,
    start: int,
    end: int,
    step: int,
) -> MetricResult:
    try:
        response = get_json(
            base_url,
            "/api/v1/query_range",
            {"query": binding.query, "start": start, "end": end, "step": step},
        )
        values = values_from_response(response) if response.get("status") == "success" else []
        if not values:
            return MetricResult(
                "NO_DATA",
                binding.key,
                binding.query,
                source_metric=binding.source_metric,
                runtime=binding.runtime,
                unit=binding.unit,
                selector_labels=binding.selector_labels,
                reason="No matching time series",
            )
        return MetricResult(
            "AVAILABLE",
            binding.key,
            binding.query,
            source_metric=binding.source_metric,
            runtime=binding.runtime,
            unit=binding.unit,
            selector_labels=binding.selector_labels,
            mean=sum(values) / len(values),
            peak=max(values),
            p50=percentile(values, 0.50),
            p90=percentile(values, 0.90),
            p95=percentile(values, 0.95),
            p99=percentile(values, 0.99),
            samples=len(values),
        )
    except TelemetryError as error:
        return MetricResult(
            "UNAVAILABLE",
            binding.key,
            binding.query,
            source_metric=binding.source_metric,
            runtime=binding.runtime,
            unit=binding.unit,
            selector_labels=binding.selector_labels,
            reason=str(error),
        )


def metric_score(name: str, patterns: tuple[str, ...]) -> int:
    for index, pattern in enumerate(patterns):
        if re.search(pattern, name, re.IGNORECASE):
            return 1000 - index * 100 - len(name)
    return -1


def ranked_candidates(
    catalog: set[str],
    patterns: tuple[str, ...],
    limit: int = MAX_ROLE_CANDIDATES,
) -> list[str]:
    scored = [(metric_score(name, patterns), name) for name in catalog]
    return [
        name
        for score, name in sorted(scored, key=lambda item: (-item[0], item[1]))
        if score >= 0
    ][:limit]


def safe_runtime_env_name(name: str) -> bool:
    upper = name.upper()
    return upper in SAFE_RUNTIME_ENV_UPPER or upper.startswith(SAFE_RUNTIME_PREFIXES)


def safe_runtime_value(name: str, entry: dict[str, Any]) -> str:
    if "value" not in entry:
        return "<valueFrom>"
    value = str(entry.get("value", ""))
    if SENSITIVE.search(name) or SENSITIVE.search(value):
        return "<redacted>"
    return value[:600] + ("..." if len(value) > 600 else "")


def container_runtimes(container: dict[str, Any]) -> set[str]:
    env_names = [
        str(item.get("name", ""))
        for item in container.get("env") or []
        if isinstance(item, dict)
    ]
    text = " ".join(
        [
            str(container.get("name", "")),
            str(container.get("image", "")),
            " ".join(map(str, container.get("command") or [])),
            " ".join(map(str, container.get("args") or [])),
            " ".join(env_names),
        ]
    )
    return {runtime for runtime, pattern in RUNTIME_DETECTION.items() if pattern.search(text)}


def workload(value: str) -> WorkloadTarget:
    namespace, separator, deployment = value.partition("/")
    if not separator or not namespace or not deployment or any(char.isspace() for char in value):
        raise argparse.ArgumentTypeError("workload must be namespace/deployment")
    return WorkloadTarget(namespace, deployment)


def workloads_file(path: str) -> list[WorkloadTarget]:
    try:
        with open(path, encoding="utf-8") as stream:
            payload = json.load(stream)
    except (OSError, json.JSONDecodeError) as error:
        raise argparse.ArgumentTypeError(f"cannot read workloads file: {error}") from error

    result: list[WorkloadTarget] = []
    for item in payload.get("items", []):
        if item.get("kind") != "Deployment":
            continue
        metadata = item.get("metadata", {})
        namespace = str(metadata.get("namespace", "default"))
        deployment = str(metadata.get("name", ""))
        if not deployment:
            continue
        pod_spec = (((item.get("spec") or {}).get("template") or {}).get("spec") or {})
        runtimes: set[str] = set()
        config: set[tuple[str, str, str]] = set()
        for container in pod_spec.get("containers") or []:
            runtimes.update(container_runtimes(container))
            container_name = str(container.get("name", "-"))
            for entry in container.get("env") or []:
                if not isinstance(entry, dict):
                    continue
                name = str(entry.get("name", ""))
                if safe_runtime_env_name(name):
                    config.add((container_name, name, safe_runtime_value(name, entry)))
        result.append(
            WorkloadTarget(
                namespace,
                deployment,
                tuple(sorted(runtimes)),
                tuple(sorted(config)),
            )
        )
    return result


def merge_targets(targets: list[WorkloadTarget]) -> list[WorkloadTarget]:
    merged: dict[tuple[str, str], dict[str, set[Any]]] = {}
    for target in targets:
        entry = merged.setdefault(
            (target.namespace, target.deployment),
            {"runtimes": set(), "config": set()},
        )
        entry["runtimes"].update(target.runtime_hints)
        entry["config"].update(target.runtime_config)
    return [
        WorkloadTarget(
            namespace,
            deployment,
            tuple(sorted(value["runtimes"])),
            tuple(sorted(value["config"])),
        )
        for (namespace, deployment), value in sorted(merged.items())
    ]


def promql_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def promql_regex_prefix(value: str) -> str:
    return re.escape(value).replace(r"\-", "-")


def selector_text(matchers: list[str]) -> str:
    return "{" + ",".join(matchers) + "}"


class MetricDiscovery:
    """Discover metric families and workload label bindings through GET-only APIs."""

    def __init__(self, base_url: str, start: int, end: int):
        self.base_url = base_url
        self.start = max(start, end - 6 * 3600)
        self.end = end
        self._lock = threading.Lock()
        self._series_cache: dict[str, list[dict[str, str]]] = {}
        self._selector_cache: dict[tuple[str, str, str, str, bool], SelectorMatch | None] = {}
        self.series_errors: list[str] = []
        self.catalog_source = "fallback"
        self.catalog_reason = ""
        self.catalog = self._catalog()

    def _catalog(self) -> set[str]:
        errors: list[str] = []
        try:
            response = get_json(self.base_url, "/api/v1/label/__name__/values")
            names = response.get("data") if response.get("status") == "success" else None
            if isinstance(names, list) and names:
                self.catalog_source = "/api/v1/label/__name__/values"
                return {str(name) for name in names if METRIC_NAME.fullmatch(str(name))}
        except TelemetryError as error:
            errors.append(str(error))
        try:
            response = get_json(self.base_url, "/api/v1/metadata", {"limit": 10000})
            metadata = response.get("data") if response.get("status") == "success" else None
            if isinstance(metadata, dict) and metadata:
                self.catalog_source = "/api/v1/metadata"
                return {str(name) for name in metadata if METRIC_NAME.fullmatch(str(name))}
        except TelemetryError as error:
            errors.append(str(error))
        self.catalog_reason = "; ".join(dict.fromkeys(errors)) or "metric catalog unavailable"
        return set(STANDARD_FALLBACK_METRICS)

    def series(self, metric: str) -> list[dict[str, str]]:
        with self._lock:
            cached = self._series_cache.get(metric)
        if cached is not None:
            return cached
        try:
            response = get_json(
                self.base_url,
                "/api/v1/series",
                {"match[]": metric, "start": self.start, "end": self.end},
            )
            raw = response.get("data") if response.get("status") == "success" else []
            value = [
                {str(key): str(item) for key, item in series.items()}
                for series in raw or []
                if isinstance(series, dict)
            ]
        except TelemetryError as error:
            value = []
            with self._lock:
                self.series_errors.append(f"{metric}: {error}")
        with self._lock:
            self._series_cache[metric] = value
        return value

    @staticmethod
    def _is_namespace_label(name: str) -> bool:
        lowered = name.lower()
        return "namespace" in lowered or lowered in {"ns", "k8s_ns"}

    @staticmethod
    def _is_pod_label(name: str) -> bool:
        return "pod" in name.lower()

    @staticmethod
    def _is_workload_label(name: str) -> bool:
        lowered = name.lower()
        return any(
            token in lowered
            for token in ("deployment", "workload", "service", "application", "app", "job")
        )

    def selector_for(
        self,
        metric: str,
        target: WorkloadTarget,
        role: str = "",
        core: bool = False,
    ) -> SelectorMatch | None:
        cache_key = (metric, target.namespace, target.deployment, role, core)
        with self._lock:
            if cache_key in self._selector_cache:
                return self._selector_cache[cache_key]

        scored: list[tuple[int, dict[str, str]]] = []
        pod_prefix = target.deployment + "-"
        for labels in self.series(metric):
            namespace_labels = [
                key
                for key, value in labels.items()
                if value == target.namespace and self._is_namespace_label(key)
            ]
            pod_labels = [
                key
                for key, value in labels.items()
                if value.startswith(pod_prefix) and self._is_pod_label(key)
            ]
            workload_labels = [
                key
                for key, value in labels.items()
                if value == target.deployment and self._is_workload_label(key)
            ]
            if not pod_labels and not workload_labels:
                continue
            score = 120 * len(namespace_labels) + 160 * len(pod_labels) + 100 * len(workload_labels)
            if role in {"heap_used", "heap_max"} and any(
                str(value).lower() in {"heap", "managed", "managed_heap"}
                for value in labels.values()
            ):
                score += 40
            scored.append((score, labels))

        match: SelectorMatch | None = None
        if scored:
            _, labels = max(scored, key=lambda item: item[0])
            namespace_keys = sorted(
                [
                    key
                    for key, value in labels.items()
                    if value == target.namespace and self._is_namespace_label(key)
                ],
                key=lambda key: (key.lower() != "namespace", key),
            )
            pod_keys = sorted(
                [
                    key
                    for key, value in labels.items()
                    if value.startswith(pod_prefix) and self._is_pod_label(key)
                ],
                key=lambda key: (key.lower() != "pod", key),
            )
            workload_keys = sorted(
                [
                    key
                    for key, value in labels.items()
                    if value == target.deployment and self._is_workload_label(key)
                ],
                key=lambda key: (
                    key.lower()
                    not in {"deployment", "workload", "service", "application", "app", "job"},
                    key,
                ),
            )
            matchers: list[str] = []
            label_names: list[str] = []
            if namespace_keys:
                key = namespace_keys[0]
                matchers.append(f'{key}="{promql_string(target.namespace)}"')
                label_names.append(key)
            matched_by = ""
            if pod_keys:
                key = pod_keys[0]
                pattern = promql_string(promql_regex_prefix(target.deployment) + "-.*")
                matchers.append(f'{key}=~"{pattern}"')
                label_names.append(key)
                matched_by = "pod-prefix"
            elif workload_keys:
                key = workload_keys[0]
                matchers.append(f'{key}="{promql_string(target.deployment)}"')
                label_names.append(key)
                matched_by = "workload-label"
            if role in {"heap_used", "heap_max"}:
                heap_labels = [
                    (key, value)
                    for key, value in labels.items()
                    if key not in label_names
                    and str(value).lower() in {"heap", "managed", "managed_heap"}
                ]
                if heap_labels:
                    key, value = sorted(heap_labels)[0]
                    matchers.append(f'{key}="{promql_string(value)}"')
                    label_names.append(key)
            if core:
                container_keys = sorted(
                    key for key in labels if key.lower() in {"container", "container_name"}
                )
                if container_keys:
                    key = container_keys[0]
                    matchers.extend((f'{key}!=""', f'{key}!="POD"'))
                    label_names.append(key)
            match = SelectorMatch(
                selector_text(matchers),
                tuple(label_names),
                matched_by,
                labels,
            )

        with self._lock:
            self._selector_cache[cache_key] = match
        return match

    @staticmethod
    def standard_selector(target: WorkloadTarget, core: bool = False) -> SelectorMatch:
        matchers = [
            f'namespace="{promql_string(target.namespace)}"',
            f'pod=~"{promql_string(promql_regex_prefix(target.deployment) + "-.*")}"',
        ]
        labels = ["namespace", "pod"]
        if core:
            matchers.extend(('container!=""', 'container!="POD"'))
            labels.append("container")
        return SelectorMatch(
            selector_text(matchers),
            tuple(labels),
            "standard-label-fallback",
            {},
        )

    def summary(self, candidates: dict[str, dict[str, list[str]]]) -> dict[str, Any]:
        errors = list(dict.fromkeys(self.series_errors))
        return {
            "state": "AVAILABLE" if self.catalog_source != "fallback" and not errors else "PARTIAL",
            "catalogSource": self.catalog_source,
            "catalogMetrics": len(self.catalog),
            "catalogReason": self.catalog_reason,
            "seriesErrors": errors[:20],
            "runtimeCandidates": candidates,
            "automaticEndpointDiscovery": False,
            "readOnlyEndpoints": [
                "/api/v1/status/runtimeinfo",
                "/api/v1/label/__name__/values",
                "/api/v1/metadata",
                "/api/v1/series",
                "/api/v1/query_range",
            ],
        }


def candidate_map(discovery: MetricDiscovery) -> dict[str, dict[str, list[str]]]:
    return {
        runtime: {
            role: ranked_candidates(discovery.catalog, patterns)
            for role, patterns in roles.items()
        }
        for runtime, roles in RUNTIME_RULES.items()
    }


def best_selector(
    discovery: MetricDiscovery,
    target: WorkloadTarget,
    names: list[str],
    role: str,
    core: bool,
    allow_standard_fallback: bool,
) -> tuple[str, SelectorMatch] | None:
    for name in names:
        match = discovery.selector_for(name, target, role, core)
        if match:
            return name, match
    if allow_standard_fallback and names:
        return names[0], discovery.standard_selector(target, core)
    return None


def core_bindings(
    discovery: MetricDiscovery,
    target: WorkloadTarget,
) -> tuple[dict[str, MetricBinding], dict[str, MetricResult]]:
    bindings: dict[str, MetricBinding] = {}
    missing: dict[str, MetricResult] = {}
    resolved: dict[str, tuple[str, SelectorMatch] | None] = {}
    for role, patterns in CORE_RULES.items():
        names = ranked_candidates(discovery.catalog, patterns, 3)
        resolved[role] = best_selector(discovery, target, names, role, True, True)

    expressions = {
        "cpu": lambda metric, selector: f"sum(rate({metric}{selector}[5m]))",
        "memory": lambda metric, selector: f"sum({metric}{selector})",
        "memory_working_set": lambda metric, selector: f"sum({metric}{selector})",
    }
    for key, builder in expressions.items():
        value = resolved.get(key)
        if not value:
            missing[key] = no_data(key, "Compatible metric family not discovered")
            continue
        metric, match = value
        bindings[key] = MetricBinding(
            key,
            builder(metric, match.selector),
            metric,
            unit="cores" if key == "cpu" else "bytes",
            selector_labels=match.labels,
        )

    throttled = resolved.get("cpu_throttled")
    periods = resolved.get("cpu_periods")
    if throttled and periods:
        throttled_metric, throttled_match = throttled
        periods_metric, periods_match = periods
        query = (
            f"100 * sum(rate({throttled_metric}{throttled_match.selector}[5m])) "
            f"/ clamp_min(sum(rate({periods_metric}{periods_match.selector}[5m])), 0.001)"
        )
        bindings["cpu_throttling"] = MetricBinding(
            "cpu_throttling",
            query,
            f"{throttled_metric}/{periods_metric}",
            unit="percent",
            selector_labels=tuple(sorted(set(throttled_match.labels + periods_match.labels))),
        )
    else:
        missing["cpu_throttling"] = no_data(
            "cpu_throttling",
            "Compatible throttled/period metric pair not discovered",
        )
    return bindings, missing


def runtime_expression(metric: str, selector: str, role: str) -> str:
    series = f"{metric}{selector}"
    lowered = metric.lower()
    if role == "gc":
        if "ratio" in lowered or "overhead" in lowered:
            return f"100 * avg({series})"
        if lowered.endswith(("_sum", "_total", "_count")):
            return f"100 * sum(rate({series}[5m]))"
        return f"100 * avg({series})"
    if role in {"allocation", "exceptions"}:
        return f"sum(rate({series}[5m]))"
    if role == "heap_max" and "jvm" in lowered:
        return f"sum({series} > 0)"
    return f"sum({series})"


def detect_runtime_metrics(
    discovery: MetricDiscovery,
    target: WorkloadTarget,
    candidates: dict[str, dict[str, list[str]]],
) -> tuple[list[str], dict[str, str], dict[str, SelectorMatch]]:
    runtimes = set(target.runtime_hints)
    detected_by = {runtime: "manifest" for runtime in target.runtime_hints}
    identity_matches: dict[str, SelectorMatch] = {}
    for runtime in RUNTIME_RULES:
        for role in ("info", "heap_used"):
            for metric in candidates[runtime].get(role, []):
                match = discovery.selector_for(metric, target, role, False)
                if match:
                    runtimes.add(runtime)
                    detected_by[runtime] = (
                        "manifest+prometheus"
                        if runtime in target.runtime_hints
                        else "prometheus"
                    )
                    if role == "info":
                        identity_matches[runtime] = match
                    break
            if runtime in runtimes and runtime in identity_matches:
                break
    return sorted(runtimes), detected_by, identity_matches


def runtime_bindings(
    discovery: MetricDiscovery,
    target: WorkloadTarget,
    runtimes: list[str],
    candidates: dict[str, dict[str, list[str]]],
) -> tuple[dict[str, MetricBinding], dict[str, MetricResult], dict[str, dict[str, str]]]:
    bindings: dict[str, MetricBinding] = {}
    missing: dict[str, MetricResult] = {}
    sources: dict[str, dict[str, str]] = {}
    for runtime in runtimes:
        prefix = RUNTIME_PREFIX[runtime]
        for role in ROLE_OUTPUT:
            if role not in RUNTIME_RULES[runtime]:
                continue
            key = f"{prefix}_{ROLE_OUTPUT[role]}"
            names = candidates[runtime].get(role, [])
            resolved = best_selector(
                discovery,
                target,
                names,
                role,
                False,
                runtime in target.runtime_hints,
            )
            if not resolved:
                missing[key] = no_data(
                    key,
                    f"No compatible {runtime} {role} metric discovered for workload",
                    runtime=runtime,
                    unit=ROLE_UNITS[role],
                )
                continue
            metric, match = resolved
            bindings[key] = MetricBinding(
                key,
                runtime_expression(metric, match.selector, role),
                metric,
                runtime=runtime,
                unit=ROLE_UNITS[role],
                selector_labels=match.labels,
            )
            sources[key] = {
                "sourceMetric": metric,
                "matchedBy": match.matched_by,
                "selectorLabels": ",".join(match.labels),
            }
    return bindings, missing, sources


def runtime_metadata(identity: SelectorMatch | None) -> dict[str, str]:
    if not identity:
        return {}
    return {
        key: value
        for key, value in identity.series_labels.items()
        if key in RUNTIME_METADATA_KEYS and not SENSITIVE.search(key)
    }


def collect_workload(
    base_url: str,
    target: WorkloadTarget,
    start: int,
    end: int,
    step: int,
    discovery: MetricDiscovery,
    candidates: dict[str, dict[str, list[str]]],
) -> dict[str, Any]:
    runtimes, detected_by, identity_matches = detect_runtime_metrics(
        discovery, target, candidates
    )
    core, missing_core = core_bindings(discovery, target)
    runtime, missing_runtime, bindings_by_key = runtime_bindings(
        discovery, target, runtimes, candidates
    )
    bindings = {**core, **runtime}
    metrics: dict[str, dict[str, Any]] = {
        key: asdict(result)
        for key, result in {**missing_core, **missing_runtime}.items()
    }
    if bindings:
        with ThreadPoolExecutor(max_workers=min(16, len(bindings))) as pool:
            futures = [
                pool.submit(collect_metric, base_url, binding, start, end, step)
                for binding in bindings.values()
            ]
            for future in as_completed(futures):
                result = future.result()
                metrics[result.metric] = asdict(result)

    core_states = {
        (metrics.get("cpu") or {}).get("state", "NO_DATA"),
        (metrics.get("memory") or {}).get("state", "NO_DATA"),
    }
    state = (
        "AVAILABLE"
        if core_states == {"AVAILABLE"}
        else "UNAVAILABLE"
        if core_states <= {"UNAVAILABLE", "NO_DATA"}
        else "PARTIAL"
    )

    runtime_telemetry: list[dict[str, Any]] = []
    for runtime_name in runtimes:
        prefix = RUNTIME_PREFIX[runtime_name]
        role_metrics = {
            role: metrics.get(f"{prefix}_{output}")
            for role, output in ROLE_OUTPUT.items()
            if f"{prefix}_{output}" in metrics
        }
        states = {
            value.get("state")
            for value in role_metrics.values()
            if isinstance(value, dict)
        }
        runtime_state = (
            "AVAILABLE"
            if "AVAILABLE" in states
            else "UNAVAILABLE"
            if "UNAVAILABLE" in states
            else "NO_DATA"
        )
        runtime_telemetry.append(
            {
                "runtime": runtime_name,
                "state": runtime_state,
                "detectedBy": detected_by.get(runtime_name, "manifest"),
                "metadata": runtime_metadata(identity_matches.get(runtime_name)),
                "metrics": role_metrics,
            }
        )

    return {
        "namespace": target.namespace,
        "deployment": target.deployment,
        "podRegex": f"{target.deployment}-.*",
        "state": state,
        "runtimeHints": list(target.runtime_hints),
        "runtimeDetected": runtimes,
        "runtimeConfig": [
            {"container": container, "name": name, "value": value}
            for container, name, value in target.runtime_config
        ],
        "runtimeTelemetry": runtime_telemetry,
        "metricBindings": bindings_by_key,
        "metrics": metrics,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only Prometheus telemetry with automatic metric/label discovery"
    )
    parser.add_argument("--url", default=os.getenv("PROMETHEUS_URL", ""))
    parser.add_argument(
        "--window",
        choices=WINDOWS,
        default=os.getenv("PROMETHEUS_WINDOW", "7d"),
    )
    parser.add_argument(
        "--workload",
        dest="workloads",
        action="append",
        type=workload,
        default=[],
    )
    parser.add_argument("--workloads-file", help="Kubernetes List JSON; Deployments are read")
    parser.add_argument(
        "--workers",
        type=int,
        default=int(os.getenv("PROMETHEUS_WORKERS", "3")),
    )
    args = parser.parse_args()

    if not args.url:
        print(
            json.dumps(
                {
                    "state": "DISABLED",
                    "reason": "PROMETHEUS_URL is not configured",
                    "workloads": [],
                },
                indent=2,
            )
        )
        return 0
    if not 1 <= args.workers <= 16:
        parser.error("--workers must be between 1 and 16")

    try:
        base_url = validate_url(args.url)
        runtime = get_json(base_url, "/api/v1/status/runtimeinfo")
        if runtime.get("status") != "success":
            raise TelemetryError("runtimeinfo response was not successful")
    except TelemetryError as error:
        print(
            json.dumps(
                {"state": "UNAVAILABLE", "reason": str(error), "workloads": []},
                indent=2,
            )
        )
        return 0

    configured = list(args.workloads)
    if args.workloads_file:
        configured.extend(workloads_file(args.workloads_file))
    targets = merge_targets(configured)
    if not targets:
        print(
            json.dumps(
                {
                    "state": "AVAILABLE",
                    "window": args.window,
                    "stepSeconds": WINDOWS[args.window],
                    "runtime": runtime.get("data", {}),
                    "workloads": [],
                    "reason": "No workloads explicitly configured",
                },
                indent=2,
            )
        )
        return 0

    end = int(time.time())
    start = end - int(args.window[:-1]) * 86400
    step = WINDOWS[args.window]
    discovery = MetricDiscovery(base_url, start, end)
    candidates = candidate_map(discovery)

    with ThreadPoolExecutor(max_workers=min(args.workers, len(targets))) as pool:
        futures = [
            pool.submit(
                collect_workload,
                base_url,
                target,
                start,
                end,
                step,
                discovery,
                candidates,
            )
            for target in targets
        ]
        items = [future.result() for future in as_completed(futures)]
    items.sort(key=lambda item: (item["namespace"], item["deployment"]))

    states = {item["state"] for item in items}
    state = (
        "AVAILABLE"
        if states == {"AVAILABLE"}
        else "UNAVAILABLE"
        if states == {"UNAVAILABLE"}
        else "PARTIAL"
    )
    runtime_counts: dict[str, int] = {}
    for item in items:
        for runtime_name in item.get("runtimeDetected", []):
            runtime_counts[runtime_name] = runtime_counts.get(runtime_name, 0) + 1

    print(
        json.dumps(
            {
                "schemaVersion": "2.0",
                "state": state,
                "window": args.window,
                "stepSeconds": step,
                "runtime": runtime.get("data", {}),
                "metricDiscovery": discovery.summary(candidates),
                "runtimeWorkloads": runtime_counts,
                "workloads": items,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
