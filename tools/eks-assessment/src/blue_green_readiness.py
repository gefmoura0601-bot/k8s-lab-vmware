#!/usr/bin/env python3
"""Provider-neutral, read-only blue-green readiness evidence."""

from __future__ import annotations

import argparse
import datetime as dt
import http.client
import ipaddress
import json
import os
import re
import socket
import ssl
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


WORKLOAD_KINDS = {"Deployment", "StatefulSet", "DaemonSet", "Rollout", "Job", "CronJob", "Pod"}
ROUTE_FILES = {
    "HTTPRoute": "httproutes.json", "GRPCRoute": "grpcroutes.json", "TLSRoute": "tlsroutes.json",
    "TCPRoute": "tcproutes.json", "UDPRoute": "udproutes.json",
}
PROHIBITED_HOSTS = {"169.254.169.254", "metadata.google.internal", "metadata.google.internal."}
FULL_NAMESPACE_SCOPES = {"", "*", "all", "cluster", "cluster-wide"}
CLIENT_WORKLOAD_HINTS = {
    "client", "cli", "e2e", "fixture", "test", "tester", "migration", "migrate",
    "backup", "restore", "exporter", "tool", "tools", "debug",
}
MANUAL_EVIDENCE_FIELDS = {
    "schemaVersion", "generatedAt", "readOnly", "changeReference", "approvalReference",
    "dnsValidated", "loadBalancerValidated", "restoreTested", "schemaBackwardCompatible",
    "singletonJobsControlled", "rollbackAvailable", "pointOfNoReturn", "dataReplication",
}


def utc_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def load(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict) and isinstance(value.get("items"), list):
        return [item for item in value["items"] if isinstance(item, dict)]
    return []


def nested(value: Any, *keys: str, fallback: Any = None) -> Any:
    current = value
    for key in keys:
        if not isinstance(current, dict):
            return fallback
        current = current.get(key)
    return fallback if current is None else current


def metadata(value: dict[str, Any]) -> dict[str, Any]:
    return value.get("metadata") or {}


def object_ref(value: dict[str, Any]) -> tuple[str, str, str]:
    meta = metadata(value)
    return str(meta.get("namespace") or "default"), str(value.get("kind") or "Unknown"), str(meta.get("name") or "-")


def collection_namespace_scope(collection: Path) -> set[str] | None:
    """Return None for cluster-wide evidence, otherwise the collected namespaces."""
    value = load(collection / "metadata.json", {}).get("namespaceScope")
    if isinstance(value, list):
        namespaces = {str(item).strip() for item in value if str(item).strip()}
        return None if not namespaces or any(item.lower() in FULL_NAMESPACE_SCOPES for item in namespaces) else namespaces
    scope = str(value or "").strip()
    if scope.lower() in FULL_NAMESPACE_SCOPES:
        return None
    return {item.strip() for item in scope.split(",") if item.strip()}


def atomic_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def manual_evidence(collection: Path) -> tuple[dict[str, Any], str]:
    path = collection / "migration-evidence.json"
    if not path.is_file():
        return {}, "NOT_PROVIDED"
    value = load(path, {})
    valid = (
        isinstance(value, dict)
        and value.get("schemaVersion") == "1.0"
        and value.get("readOnly") is True
        and isinstance(value.get("generatedAt"), str)
        and bool(value.get("generatedAt"))
        and set(value).issubset(MANUAL_EVIDENCE_FIELDS)
    )
    for key in ("dnsValidated", "loadBalancerValidated", "restoreTested", "schemaBackwardCompatible", "singletonJobsControlled", "rollbackAvailable"):
        if key in value and not isinstance(value.get(key), bool):
            valid = False
    replication = value.get("dataReplication")
    if replication is not None and (
        not isinstance(replication, dict)
        or replication.get("state") not in {"READY", "FAILED", "UNKNOWN"}
        or not set(replication).issubset({"state", "detail"})
    ):
        valid = False
    return (value, "VALID") if valid else ({}, "INVALID")


def workload_documents(collection: Path) -> list[dict[str, Any]]:
    documents = items(load(collection / "application-manifests-sanitized.json", {}))
    if documents:
        return [item for item in documents if item.get("kind") in WORKLOAD_KINDS]
    output: list[dict[str, Any]] = []
    for name in ("workloads.json", "jobs.json", "cronjobs.json", "rollouts.json"):
        output.extend(item for item in items(load(collection / name, {})) if item.get("kind") in WORKLOAD_KINDS)
    return output


def pod_spec(value: dict[str, Any]) -> dict[str, Any]:
    kind = value.get("kind")
    spec = value.get("spec") or {}
    if kind == "Pod":
        return spec
    if kind == "CronJob":
        return nested(spec, "jobTemplate", "spec", "template", "spec", fallback={}) or {}
    return nested(spec, "template", "spec", fallback={}) or {}


def add_reference(output: list[dict[str, Any]], source: dict[str, str], target_kind: str, name: Any, usage: str, *, key: Any = None, optional: bool = False, namespace: str | None = None) -> None:
    name = str(name or "").strip()
    if not name:
        return
    output.append({
        "source": source,
        "target": {"kind": target_kind, "namespace": namespace or source["namespace"], "name": name, **({"key": str(key)} if key else {})},
        "usage": usage,
        "optional": bool(optional),
    })


def configuration_references(collection: Path) -> dict[str, Any]:
    references: list[dict[str, Any]] = []
    for workload in workload_documents(collection):
        namespace, kind, name = object_ref(workload)
        base_source = {"kind": kind, "namespace": namespace, "name": name}
        spec = pod_spec(workload)
        for image_pull in spec.get("imagePullSecrets") or []:
            add_reference(references, base_source, "Secret", image_pull.get("name"), "imagePullSecret")
        for volume in spec.get("volumes") or []:
            volume_name = str(volume.get("name") or "-")
            source = {**base_source, "field": f"volume/{volume_name}"}
            if isinstance(volume.get("secret"), dict):
                secret = volume["secret"]
                add_reference(references, source, "Secret", secret.get("secretName"), "volume", optional=secret.get("optional") is True)
            if isinstance(volume.get("configMap"), dict):
                config = volume["configMap"]
                add_reference(references, source, "ConfigMap", config.get("name"), "volume", optional=config.get("optional") is True)
            for projected in nested(volume, "projected", "sources", fallback=[]) or []:
                for field, target_kind in (("secret", "Secret"), ("configMap", "ConfigMap")):
                    ref = projected.get(field) or {}
                    add_reference(references, source, target_kind, ref.get("name"), "projectedVolume", optional=ref.get("optional") is True)
        for container_type in ("containers", "initContainers", "ephemeralContainers"):
            for container in spec.get(container_type) or []:
                source = {**base_source, "container": str(container.get("name") or "-"), "field": container_type}
                for env_from in container.get("envFrom") or []:
                    for field, target_kind in (("secretRef", "Secret"), ("configMapRef", "ConfigMap")):
                        ref = env_from.get(field) or {}
                        add_reference(references, source, target_kind, ref.get("name"), "envFrom", optional=ref.get("optional") is True)
                for env in container.get("env") or []:
                    value_from = env.get("valueFrom") or {}
                    for field, target_kind in (("secretKeyRef", "Secret"), ("configMapKeyRef", "ConfigMap")):
                        ref = value_from.get(field) or {}
                        add_reference(references, source, target_kind, ref.get("name"), "env", key=ref.get("key"), optional=ref.get("optional") is True)
    for ingress in items(load(collection / "ingresses.json", {})):
        namespace, kind, name = object_ref(ingress)
        source = {"kind": kind, "namespace": namespace, "name": name}
        for tls in (ingress.get("spec") or {}).get("tls") or []:
            add_reference(references, source, "Secret", tls.get("secretName"), "ingressTLS")
    for gateway in [*items(load(collection / "gateways.json", {})), *items(load(collection / "istio-gateways.json", {}))]:
        namespace, kind, name = object_ref(gateway)
        source = {"kind": kind, "namespace": namespace, "name": name}
        for listener in (gateway.get("spec") or {}).get("listeners") or []:
            for ref in nested(listener, "tls", "certificateRefs", fallback=[]) or []:
                if str(ref.get("kind") or "Secret") == "Secret":
                    add_reference(references, source, "Secret", ref.get("name"), "gatewayTLS", namespace=ref.get("namespace") or namespace)
        credential = nested(gateway, "spec", "servers", fallback=[]) or []
        for server in credential:
            add_reference(references, source, "Secret", nested(server, "tls", "credentialName"), "istioGatewayTLS")
    for certificate in items(load(collection / "certificates.json", {})):
        namespace, kind, name = object_ref(certificate)
        add_reference(references, {"kind": kind, "namespace": namespace, "name": name}, "Secret", nested(certificate, "spec", "secretName"), "certificateOutput")

    inventory = load(collection / "configuration-metadata.json", {})
    inventory_state = {key[:-1].capitalize() if key == "secrets" else "ConfigMap": value for key, value in (inventory.get("resources") or {}).items()}
    objects = {(str(item.get("kind")), str(item.get("namespace")), str(item.get("name"))): item for item in inventory.get("items") or []}
    unique: dict[tuple[str, ...], dict[str, Any]] = {}
    for reference in references:
        source, target = reference["source"], reference["target"]
        identity = (source["kind"], source["namespace"], source["name"], source.get("container", ""), source.get("field", ""), target["kind"], target["namespace"], target["name"], target.get("key", ""), reference["usage"])
        available = str((inventory_state.get(target["kind"]) or {}).get("state")) == "AVAILABLE"
        target_object = objects.get((target["kind"], target["namespace"], target["name"]))
        if not available:
            state, detail = "EVIDENCE_UNAVAILABLE", "Metadata opt-in não coletada ou RBAC indisponível."
        elif not target_object:
            state, detail = ("WARN" if reference["optional"] else "MISSING"), "Objeto referenciado não encontrado."
        elif target.get("key") and target["key"] not in (target_object.get("keys") or []):
            state, detail = ("WARN" if reference["optional"] else "MISSING"), "Chave referenciada não encontrada."
        else:
            state, detail = "RESOLVED", "Objeto e chave confirmados sem persistir valores."
        unique[identity] = {**reference, "state": state, "detail": detail, "evidenceSource": "ConfigurationMetadata" if available else "WorkloadReference"}
    rows = sorted(unique.values(), key=lambda x: (x["state"], x["source"]["namespace"], x["source"]["name"], x["target"]["kind"], x["target"]["name"]))
    counts = Counter(item["state"] for item in rows)
    report_state = "FAIL" if counts["MISSING"] else "EVIDENCE_UNAVAILABLE" if counts["EVIDENCE_UNAVAILABLE"] else "WARN" if counts["WARN"] else "PASS"
    return {
        "schemaVersion": "1.0", "generatedAt": utc_iso(), "readOnly": True, "state": report_state,
        "policy": {"secretValues": "NOT_COLLECTED", "configMapValues": "NOT_COLLECTED", "metadataOptIn": bool(inventory)},
        "summary": {"references": len(rows), "resolved": counts["RESOLVED"], "missing": counts["MISSING"], "warnings": counts["WARN"], "evidenceUnavailable": counts["EVIDENCE_UNAVAILABLE"]},
        "references": rows,
    }


def ready_endpoints(collection: Path) -> dict[tuple[str, str], int]:
    output: dict[tuple[str, str], int] = defaultdict(int)
    for endpoint_slice in items(load(collection / "endpointslices.json", {})):
        meta = metadata(endpoint_slice)
        service = str((meta.get("labels") or {}).get("kubernetes.io/service-name") or "")
        namespace = str(meta.get("namespace") or "default")
        for endpoint in endpoint_slice.get("endpoints") or []:
            if endpoint.get("addresses") and nested(endpoint, "conditions", "ready", fallback=True) is not False:
                output[(namespace, service)] += 1
    return output


def condition_states(value: dict[str, Any]) -> set[str]:
    return {
        str(item.get("type"))
        for parent in nested(value, "status", "parents", fallback=[]) or []
        for item in parent.get("conditions") or []
        if str(item.get("status")) == "True"
    } | {
        str(item.get("type"))
        for item in nested(value, "status", "conditions", fallback=[]) or []
        if str(item.get("status")) == "True"
    }


def namespace_evidence_available(scope: set[str] | None, namespace: str) -> bool:
    return scope is None or namespace in scope


def service_target(
    services: dict[tuple[str, str], dict[str, Any]],
    endpoints: dict[tuple[str, str], int],
    namespace: str,
    name: str,
    port: Any = None,
    *,
    evidence_available: bool = True,
) -> dict[str, Any]:
    service = services.get((namespace, name))
    if not service:
        state = "FAIL" if evidence_available else "UNKNOWN"
        reason = "Service não encontrado" if evidence_available else "Service fora do escopo de namespaces coletado"
        return {"state": state, "reason": reason, "service": f"{namespace}/{name}", "readyEndpoints": 0, "port": port}
    ports = (service.get("spec") or {}).get("ports") or []
    port_ok = port in {None, ""} or any(str(item.get("name")) == str(port) or str(item.get("port")) == str(port) for item in ports)
    ready = endpoints.get((namespace, name), 0)
    state = "PASS" if port_ok and (ready > 0 or not (service.get("spec") or {}).get("selector")) else "FAIL"
    reason = "Destino resolvido" if state == "PASS" else "Porta ausente" if not port_ok else "Nenhum EndpointSlice ready"
    return {"state": state, "reason": reason, "service": f"{namespace}/{name}", "readyEndpoints": ready, "port": port}


def reference_granted(grants: list[dict[str, Any]], source_namespace: str, source_kind: str, target_namespace: str, target_name: str) -> bool:
    """Evaluate the ReferenceGrant fields relevant to a Gateway API Service backend."""
    for grant in grants:
        if object_ref(grant)[0] != target_namespace:
            continue
        spec = grant.get("spec") or {}
        from_match = any(
            str(item.get("group") or "gateway.networking.k8s.io") == "gateway.networking.k8s.io"
            and str(item.get("kind") or "") == source_kind
            and str(item.get("namespace") or "") == source_namespace
            for item in spec.get("from") or []
        )
        to_match = any(
            str(item.get("group") or "") in {"", "core"}
            and str(item.get("kind") or "") == "Service"
            and (not item.get("name") or str(item.get("name")) == target_name)
            for item in spec.get("to") or []
        )
        if from_match and to_match:
            return True
    return False


def gateway_parent_check(
    gateway: dict[str, Any] | None,
    route_namespace: str,
    parent: dict[str, Any],
    *,
    evidence_available: bool = True,
) -> dict[str, Any]:
    parent_namespace = str(parent.get("namespace") or route_namespace)
    parent_name = str(parent.get("name") or "")
    if not gateway:
        state = "FAIL" if evidence_available else "UNKNOWN"
        detail = f"Gateway {parent_namespace}/{parent_name} ausente" if evidence_available else f"Gateway {parent_namespace}/{parent_name} fora do escopo de namespaces coletado"
        return {"state": state, "check": "parentRef", "detail": detail}
    listeners = (gateway.get("spec") or {}).get("listeners") or []
    section_name = str(parent.get("sectionName") or "")
    if section_name:
        listeners = [item for item in listeners if str(item.get("name") or "") == section_name]
        if not listeners:
            return {"state": "FAIL", "check": "parentRef", "detail": f"Listener {section_name} não encontrado em {parent_namespace}/{parent_name}"}
    if parent_namespace == route_namespace:
        return {"state": "PASS", "check": "parentRef", "detail": f"Gateway {parent_namespace}/{parent_name} encontrado"}
    policies = [nested(listener, "allowedRoutes", "namespaces", "from", fallback="Same") for listener in listeners]
    if "All" in policies:
        return {"state": "PASS", "check": "allowedRoutes", "detail": f"Gateway {parent_namespace}/{parent_name} permite namespaces diferentes"}
    if "Selector" in policies:
        return {"state": "UNKNOWN", "check": "allowedRoutes", "detail": "Selector de namespace exige confirmação dos labels e status Accepted."}
    return {"state": "FAIL", "check": "allowedRoutes", "detail": f"Gateway {parent_namespace}/{parent_name} não permite rota de {route_namespace}"}


def route_status(path: dict[str, Any]) -> str:
    states = [target.get("state") for target in path.get("targets") or []]
    checks = path.get("checks") or []
    if "FAIL" in states or any(item.get("state") == "FAIL" for item in checks):
        return "FAIL"
    if "UNKNOWN" in states or any(item.get("state") == "UNKNOWN" for item in checks):
        return "UNKNOWN"
    if "WARN" in states or any(item.get("state") == "WARN" for item in checks):
        return "WARN"
    return "PASS"


def traffic_paths(collection: Path, config: dict[str, Any]) -> dict[str, Any]:
    namespace_scope = collection_namespace_scope(collection)
    services = {(object_ref(item)[0], object_ref(item)[2]): item for item in items(load(collection / "services.json", {}))}
    endpoints = ready_endpoints(collection)
    paths: list[dict[str, Any]] = []
    for ingress in items(load(collection / "ingresses.json", {})):
        namespace, kind, name = object_ref(ingress)
        spec = ingress.get("spec") or {}
        tls_hosts = {host for row in spec.get("tls") or [] for host in row.get("hosts") or []}
        rules = spec.get("rules") or [{"host": "*", "http": {"paths": [{"path": "/", "backend": spec.get("defaultBackend") or {}}]}}]
        for rule in rules:
            host = str(rule.get("host") or "*")
            for route in nested(rule, "http", "paths", fallback=[]) or []:
                backend = route.get("backend") or {}
                service_name = str(nested(backend, "service", "name", fallback="") or backend.get("serviceName") or "")
                port = nested(backend, "service", "port", "name") or nested(backend, "service", "port", "number") or backend.get("servicePort")
                path = {"type": "Ingress", "namespace": namespace, "name": name, "host": host, "path": route.get("path") or "/", "tls": host in tls_hosts, "targets": [service_target(services, endpoints, namespace, service_name, port)], "checks": []}
                if not path["tls"]:
                    path["checks"].append({"state": "WARN", "check": "TLS", "detail": "Host sem spec.tls."})
                path["state"] = route_status(path); paths.append(path)

    gateway_objects = {(object_ref(item)[0], object_ref(item)[2]): item for item in items(load(collection / "gateways.json", {}))}
    grants = items(load(collection / "referencegrants.json", {}))
    for kind, filename in ROUTE_FILES.items():
        for route_object in items(load(collection / filename, {})):
            namespace, _, name = object_ref(route_object)
            spec = route_object.get("spec") or {}
            accepted = condition_states(route_object)
            parents = spec.get("parentRefs") or []
            parent_checks = []
            for parent in parents:
                parent_ns = str(parent.get("namespace") or namespace)
                parent_name = str(parent.get("name") or "")
                parent_checks.append(gateway_parent_check(
                    gateway_objects.get((parent_ns, parent_name)),
                    namespace,
                    parent,
                    evidence_available=namespace_evidence_available(namespace_scope, parent_ns),
                ))
            if not parents:
                parent_checks.append({"state": "UNKNOWN", "check": "parentRef", "detail": "Route sem parentRef explícito."})
            if not accepted:
                parent_checks.append({"state": "UNKNOWN", "check": "status", "detail": "Conditions Accepted/ResolvedRefs não disponíveis."})
            elif not {"Accepted", "ResolvedRefs"}.issubset(accepted):
                parent_checks.append({"state": "FAIL", "check": "status", "detail": f"Conditions={','.join(sorted(accepted)) or 'none'}"})
            rules = spec.get("rules") or []
            for index, rule in enumerate(rules or [{}]):
                targets = []
                for backend in rule.get("backendRefs") or []:
                    backend_kind = str(backend.get("kind") or "Service")
                    backend_ns = str(backend.get("namespace") or namespace)
                    if backend_kind != "Service":
                        targets.append({"state": "UNKNOWN", "reason": f"backend kind {backend_kind} exige validação manual", "service": f"{backend_ns}/{backend.get('name')}", "readyEndpoints": 0, "port": backend.get("port")})
                        continue
                    target = service_target(
                        services,
                        endpoints,
                        backend_ns,
                        str(backend.get("name") or ""),
                        backend.get("port"),
                        evidence_available=namespace_evidence_available(namespace_scope, backend_ns),
                    )
                    if backend_ns != namespace:
                        granted = reference_granted(grants, namespace, kind, backend_ns, str(backend.get("name") or ""))
                        if not granted:
                            grant_evidence = namespace_evidence_available(namespace_scope, backend_ns)
                            target = {
                                **target,
                                "state": "FAIL" if grant_evidence else "UNKNOWN",
                                "reason": "Referência cross-namespace sem ReferenceGrant comprovado" if grant_evidence else "ReferenceGrant fora do escopo de namespaces coletado",
                            }
                    targets.append(target)
                path = {"type": kind, "namespace": namespace, "name": name, "rule": index, "hostnames": spec.get("hostnames") or [], "parents": parents, "targets": targets, "checks": list(parent_checks)}
                if not targets:
                    path["checks"].append({"state": "UNKNOWN", "check": "backendRefs", "detail": "Nenhum backendRef avaliável."})
                path["state"] = route_status(path); paths.append(path)

    destination_rules = items(load(collection / "istio-destinationrules.json", {}))
    dr_index: dict[tuple[str, str], set[str]] = {}
    for rule in destination_rules:
        namespace, _, _ = object_ref(rule)
        host = str(nested(rule, "spec", "host", fallback=""))
        dr_index[(namespace, host)] = {str(item.get("name")) for item in nested(rule, "spec", "subsets", fallback=[]) or []}
    istio_gateways = {(object_ref(item)[0], object_ref(item)[2]) for item in items(load(collection / "istio-gateways.json", {}))}
    for virtual_service in items(load(collection / "istio-virtualservices.json", {})):
        namespace, kind, name = object_ref(virtual_service)
        spec = virtual_service.get("spec") or {}
        checks = []
        for gateway in spec.get("gateways") or []:
            if gateway == "mesh":
                continue
            gateway_ns, gateway_name = (str(gateway).split("/", 1) if "/" in str(gateway) else (namespace, str(gateway)))
            gateway_found = (gateway_ns, gateway_name) in istio_gateways
            evidence_available = namespace_evidence_available(namespace_scope, gateway_ns)
            checks.append({
                "state": "PASS" if gateway_found else "FAIL" if evidence_available else "UNKNOWN",
                "check": "gateway",
                "detail": f"{gateway_ns}/{gateway_name}" if gateway_found or evidence_available else f"{gateway_ns}/{gateway_name} fora do escopo de namespaces coletado",
            })
        for index, route in enumerate([*(spec.get("http") or []), *(spec.get("tcp") or []), *(spec.get("tls") or [])]):
            destinations = nested(route, "route", fallback=[]) or []
            targets = []
            weights = []
            for destination in destinations:
                target_spec = destination.get("destination") or {}
                host = str(target_spec.get("host") or "")
                parts = host.split(".")
                target_ns = parts[1] if len(parts) >= 2 and parts[1] not in {"svc", "cluster"} else namespace
                target_name = parts[0]
                target = service_target(
                    services,
                    endpoints,
                    target_ns,
                    target_name,
                    nested(target_spec, "port", "number"),
                    evidence_available=namespace_evidence_available(namespace_scope, target_ns),
                )
                subset = str(target_spec.get("subset") or "")
                if subset:
                    subset_exists = subset in (dr_index.get((namespace, host)) or dr_index.get((target_ns, host)) or set())
                    if not subset_exists:
                        target = {**target, "state": "FAIL", "reason": f"Subset {subset} não encontrado em DestinationRule"}
                targets.append(target)
                if destination.get("weight") is not None:
                    weights.append(int(destination.get("weight") or 0))
            local_checks = list(checks)
            if weights and sum(weights) != 100:
                local_checks.append({"state": "FAIL", "check": "weights", "detail": f"Soma dos weights={sum(weights)}; esperado=100"})
            path = {"type": "VirtualService", "namespace": namespace, "name": name, "route": index, "hosts": spec.get("hosts") or [], "targets": targets, "checks": local_checks}
            if not targets:
                path["checks"].append({"state": "UNKNOWN", "check": "destinations", "detail": "Nenhum destino avaliável."})
            path["state"] = route_status(path); paths.append(path)

    for route_object in items(load(collection / "openshift-routes.json", {})):
        namespace, kind, name = object_ref(route_object)
        target_name = str(nested(route_object, "spec", "to", "name", fallback=""))
        target = service_target(services, endpoints, namespace, target_name, nested(route_object, "spec", "port", "targetPort"))
        path = {"type": "OpenShiftRoute", "namespace": namespace, "name": name, "host": nested(route_object, "spec", "host"), "targets": [target], "checks": []}
        path["state"] = route_status(path); paths.append(path)
    counts = Counter(item["state"] for item in paths)
    state = "FAIL" if counts["FAIL"] else "UNKNOWN" if counts["UNKNOWN"] else "WARN" if counts["WARN"] else "PASS"
    if not paths:
        state = "N/A"
    return {
        "schemaVersion": "1.0", "generatedAt": utc_iso(), "readOnly": True, "state": state,
        "summary": {"paths": len(paths), "passed": counts["PASS"], "warnings": counts["WARN"], "failed": counts["FAIL"], "unknown": counts["UNKNOWN"], "services": len(services)},
        "paths": paths,
        "evidence": {
            "dns": "EVIDENCE_UNAVAILABLE",
            "externalLoadBalancer": "MANUAL_REVIEW",
            "configurationReferences": config.get("state"),
            "namespaceScope": "*" if namespace_scope is None else sorted(namespace_scope),
        },
    }


def state_data_readiness(collection: Path) -> dict[str, Any]:
    workloads = workload_documents(collection)
    pvcs = items(load(collection / "pvcs.json", {}))
    snapshots = items(load(collection / "volumesnapshots.json", {}))
    backups = items(load(collection / "velero-backups.json", {}))
    restores = items(load(collection / "velero-restores.json", {}))
    manual, manual_state = manual_evidence(collection)
    stateful = []
    cronjobs = []
    for workload in workloads:
        namespace, kind, name = object_ref(workload)
        spec = pod_spec(workload)
        claims = sorted({str(nested(volume, "persistentVolumeClaim", "claimName")) for volume in spec.get("volumes") or [] if nested(volume, "persistentVolumeClaim", "claimName")})
        containers = spec.get("containers") or []
        images = " ".join(str(container.get("image") or "") for container in containers).lower()
        detected = [token for token in ("postgres", "mysql", "mariadb", "mongo", "kafka", "rabbitmq", "redis", "valkey") if token in f"{name} {images}".lower()]
        labels = {**(metadata(workload).get("labels") or {}), **(nested(workload, "spec", "template", "metadata", "labels", fallback={}) or {})}
        role_text = " ".join([
            name,
            *(str(value) for value in labels.values()),
            *(str(container.get("name") or "") for container in containers),
        ]).lower()
        commands = " ".join(
            str(value)
            for container in containers
            for value in [*(container.get("command") or []), *(container.get("args") or [])]
        ).lower()
        client_only = any(re.search(rf"(^|[-_.]){re.escape(hint)}($|[-_.])", role_text) for hint in CLIENT_WORKLOAD_HINTS)
        client_only = client_only or any(command in commands for command in ("psql", "mysql ", "mongosh", "redis-cli", "sleep infinity"))
        if kind == "StatefulSet" or claims or detected:
            if kind == "StatefulSet" or claims or not client_only:
                reasons = (["StatefulSet"] if kind == "StatefulSet" else []) + (["PersistentVolumeClaim"] if claims else []) + (["server-image"] if detected and not client_only else [])
                stateful.append({"namespace": namespace, "kind": kind, "name": name, "claims": claims, "technologies": detected, "detectionReasons": reasons})
        if kind in {"Job", "CronJob"}:
            cronjobs.append({"namespace": namespace, "kind": kind, "name": name})
    pvc_rows = []
    for pvc in pvcs:
        namespace, _, name = object_ref(pvc)
        phase = str(nested(pvc, "status", "phase", fallback="Unknown"))
        pvc_rows.append({"namespace": namespace, "name": name, "state": "PASS" if phase == "Bound" else "FAIL", "phase": phase, "storageClass": nested(pvc, "spec", "storageClassName"), "accessModes": nested(pvc, "spec", "accessModes", fallback=[]), "requested": nested(pvc, "spec", "resources", "requests", "storage")})
    backup_failed = sum(str(nested(item, "status", "phase", fallback="")).lower() in {"failed", "failedvalidation"} for item in backups)
    snapshot_not_ready = sum(nested(item, "status", "readyToUse") is not True for item in snapshots)
    gates = [
        {"gateId": "storage.pvc-bound", "status": "FAIL" if any(item["state"] == "FAIL" for item in pvc_rows) else "PASS", "mandatory": True, "detail": f"PVCs={len(pvc_rows)}; não Bound={sum(item['state']=='FAIL' for item in pvc_rows)}"},
        {"gateId": "data.backup-health", "status": "FAIL" if backup_failed else "PASS" if backups else "UNKNOWN" if stateful else "N/A", "mandatory": bool(stateful), "detail": f"Velero backups={len(backups)}; falhos={backup_failed}; restores={len(restores)}"},
        {"gateId": "data.snapshot-health", "status": "FAIL" if snapshot_not_ready else "PASS" if snapshots else "UNKNOWN" if pvcs else "N/A", "mandatory": bool(pvcs), "detail": f"VolumeSnapshots={len(snapshots)}; não ready={snapshot_not_ready}"},
        {"gateId": "data.replication", "status": "PASS" if nested(manual, "dataReplication", "state") == "READY" else "FAIL" if nested(manual, "dataReplication", "state") == "FAILED" else "UNKNOWN" if stateful else "N/A", "mandatory": bool(stateful), "detail": str(manual.get("dataReplication") or "Evidência manual ausente")},
        {"gateId": "data.restore-tested", "status": "PASS" if manual.get("restoreTested") is True else "FAIL" if manual.get("restoreTested") is False else "UNKNOWN" if stateful else "N/A", "mandatory": bool(stateful), "detail": "Restore isolado testado" if manual.get("restoreTested") is True else "Teste de restore não comprovado"},
        {"gateId": "application.schema-compatibility", "status": "PASS" if manual.get("schemaBackwardCompatible") is True else "FAIL" if manual.get("schemaBackwardCompatible") is False else "UNKNOWN" if stateful else "N/A", "mandatory": bool(stateful), "detail": "Compatibilidade de schema declarada" if manual.get("schemaBackwardCompatible") is True else "Compatibilidade de schema não comprovada"},
        {"gateId": "application.singleton-jobs", "status": "PASS" if manual.get("singletonJobsControlled") is True else "UNKNOWN" if cronjobs else "N/A", "mandatory": bool(cronjobs), "detail": f"Jobs/CronJobs={len(cronjobs)}"},
        {"gateId": "rollback.available", "status": "PASS" if manual.get("rollbackAvailable") is True else "FAIL" if manual.get("rollbackAvailable") is False else "UNKNOWN" if stateful else "N/A", "mandatory": bool(stateful), "detail": "Rollback comprovado" if manual.get("rollbackAvailable") is True else "Rollback não comprovado"},
    ]
    mandatory = [item for item in gates if item["mandatory"]]
    state = "FAIL" if any(item["status"] == "FAIL" for item in mandatory) else "UNKNOWN" if any(item["status"] == "UNKNOWN" for item in mandatory) else "PASS"
    return {
        "schemaVersion": "1.0", "generatedAt": utc_iso(), "readOnly": True, "state": state,
        "summary": {"statefulWorkloads": len(stateful), "pvcs": len(pvc_rows), "cronJobsAndJobs": len(cronjobs), "snapshots": len(snapshots), "backups": len(backups), "restores": len(restores)},
        "statefulWorkloads": stateful, "persistentClaims": pvc_rows, "jobs": cronjobs, "gates": gates,
        "manualEvidence": {"provided": bool(manual), "state": manual_state, "path": "migration-evidence.json"},
    }


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def validate_probe_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("a URL do probe deve usar http ou https")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("a URL do probe não pode conter credenciais, query ou fragment")
    if parsed.hostname.lower() in PROHIBITED_HOSTS:
        raise ValueError("metadata endpoint não é permitido")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)}
    except socket.gaierror as error:
        raise ValueError(f"falha na resolução DNS: {error}") from error
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if ip.is_link_local or ip.is_multicast or ip.is_unspecified or str(ip) in PROHIBITED_HOSTS:
            raise ValueError("endereços link-local, multicast, unspecified e metadata não são permitidos")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", "", ""))


def safe_probe_url(value: str) -> str:
    """Keep only a non-credential URL identity for rejected probes."""
    try:
        parsed = urllib.parse.urlsplit(value.strip())
        hostname = parsed.hostname or ""
        if not hostname:
            return ""
        hostname = f"[{hostname}]" if ":" in hostname and not hostname.startswith("[") else hostname
        port = parsed.port
        netloc = f"{hostname}:{port}" if port else hostname
        return urllib.parse.urlunsplit((parsed.scheme, netloc, parsed.path or "/", "", ""))
    except (TypeError, ValueError):
        return ""


def run_probes(urls: Iterable[str], timeout: float = 5.0, max_latency_ms: float = 2000.0) -> dict[str, Any]:
    rows = []
    opener = urllib.request.build_opener(NoRedirect)
    for raw in urls:
        try:
            url = validate_probe_url(raw)
            started = time.monotonic()
            request = urllib.request.Request(url, method="GET", headers={"User-Agent": "kubernetes-assessment-blue-green/1"})
            try:
                response = opener.open(request, timeout=timeout)
                status = int(response.status); response.read(65536)
            except urllib.error.HTTPError as error:
                status = int(error.code); error.read(65536)
            latency = round((time.monotonic() - started) * 1000, 1)
            state = "PASS" if 200 <= status < 400 and latency <= max_latency_ms else "WARN" if 200 <= status < 400 else "FAIL"
            rows.append({"url": url, "state": state, "httpStatus": status, "latencyMs": latency, "tlsValidated": url.startswith("https://")})
        except (ValueError, OSError, ssl.SSLError, http.client.HTTPException, urllib.error.URLError) as error:
            rows.append({"url": safe_probe_url(raw), "state": "FAIL", "error": str(error)[:300]})
    counts = Counter(item["state"] for item in rows)
    state = "DISABLED" if not rows else "FAIL" if counts["FAIL"] else "WARN" if counts["WARN"] else "PASS"
    return {"schemaVersion": "1.0", "generatedAt": utc_iso(), "readOnly": True, "state": state, "summary": {"probes": len(rows), "passed": counts["PASS"], "warnings": counts["WARN"], "failed": counts["FAIL"], "maxLatencyMs": max_latency_ms}, "items": rows, "policy": {"method": "GET", "redirects": "DENIED", "responseBodyPersisted": False, "credentialsAllowed": False}}


def aggregate_readiness(collection: Path, config: dict[str, Any], traffic: dict[str, Any], state_data: dict[str, Any], probes: dict[str, Any]) -> dict[str, Any]:
    operational = load(collection / "operational-insights.json", {})
    manual, manual_state = manual_evidence(collection)
    node_state = str(nested(operational, "nodeHealth", "state", fallback="EVIDENCE_UNAVAILABLE"))
    gates = [
        {"gateId": "configuration.references", "domain": "Configuration", "status": "FAIL" if config["state"] == "FAIL" else "UNKNOWN" if config["state"] == "EVIDENCE_UNAVAILABLE" else config["state"], "mandatory": True, "summary": f"Referências={config['summary']['references']}; ausentes={config['summary']['missing']}; sem evidência={config['summary']['evidenceUnavailable']}"},
        {"gateId": "traffic.paths", "domain": "Traffic", "status": traffic["state"], "mandatory": traffic["state"] != "N/A", "summary": f"Paths={traffic['summary']['paths']}; falhos={traffic['summary']['failed']}; desconhecidos={traffic['summary']['unknown']}"},
        {"gateId": "state.data", "domain": "State & Data", "status": state_data["state"], "mandatory": bool(state_data["summary"]["statefulWorkloads"] or state_data["summary"]["cronJobsAndJobs"]), "summary": f"Stateful={state_data['summary']['statefulWorkloads']}; PVCs={state_data['summary']['pvcs']}"},
        {"gateId": "capacity.node-health", "domain": "Capacity", "status": "PASS" if node_state == "PASS" else "FAIL" if node_state == "CRIT" else "WARN" if node_state == "WARN" else "UNKNOWN", "mandatory": True, "summary": f"Node Health={node_state}"},
        {"gateId": "cutover.probes", "domain": "Cutover", "status": "N/A" if probes["state"] == "DISABLED" else probes["state"], "mandatory": probes["state"] != "DISABLED", "summary": f"Probes={probes['summary']['probes']}; falhos={probes['summary']['failed']}"},
        {"gateId": "cutover.dns", "domain": "Cutover", "status": "PASS" if manual.get("dnsValidated") is True and manual.get("loadBalancerValidated") is True else "FAIL" if manual.get("dnsValidated") is False or manual.get("loadBalancerValidated") is False else "UNKNOWN", "mandatory": True, "summary": "DNS, TTL e load balancer externo exigem evidência manual ou provider API."},
        {"gateId": "manual-evidence.contract", "domain": "Governance", "status": "FAIL" if manual_state == "INVALID" else "PASS" if manual_state == "VALID" else "N/A", "mandatory": manual_state == "INVALID", "summary": f"migration-evidence.json={manual_state}"},
    ]
    mandatory = [item for item in gates if item["mandatory"]]
    no_go = [item for item in mandatory if item["status"] == "FAIL"]
    unknown = [item for item in mandatory if item["status"] in {"UNKNOWN", "EVIDENCE_UNAVAILABLE"}]
    status = "NO_GO" if no_go else "UNKNOWN" if unknown else "GO"
    return {
        "schemaVersion": "1.0", "generatedAt": utc_iso(), "readOnly": True, "status": status,
        "notice": "Readiness provider-neutral para migração blue-green. GO exige evidência dos gates obrigatórios; não substitui aprovação operacional.",
        "collection": collection.name,
        "summary": {"gates": len(gates), "blocking": len(no_go), "unknown": len(unknown), "warnings": sum(item["status"] == "WARN" for item in gates)},
        "gates": gates,
        "artifacts": {"configurationReferences": "configuration-references.json", "trafficPaths": "traffic-paths.json", "stateDataReadiness": "state-data-readiness.json", "probes": "migration-probes.json"},
    }


def generate(
    collection: Path,
    probe_urls: Iterable[str] = (),
    *,
    include_environment_probes: bool = True,
) -> dict[str, Any]:
    collection = collection.resolve()
    config = configuration_references(collection)
    traffic = traffic_paths(collection, config)
    state_data = state_data_readiness(collection)
    environment_urls = [
        item.strip()
        for item in re.split(r"[,\n]", os.getenv("ASSESSMENT_PROBE_URLS", ""))
        if include_environment_probes and item.strip()
    ]
    probes = run_probes([*probe_urls, *environment_urls], max_latency_ms=float(os.getenv("ASSESSMENT_PROBE_MAX_LATENCY_MS", "2000")))
    readiness = aggregate_readiness(collection, config, traffic, state_data, probes)
    for name, value in (("configuration-references.json", config), ("traffic-paths.json", traffic), ("state-data-readiness.json", state_data), ("migration-probes.json", probes), ("blue-green-readiness.json", readiness)):
        atomic_write(collection / name, value)
    return readiness


def main() -> int:
    parser = argparse.ArgumentParser(description="Gera readiness provider-neutral para migração blue-green")
    parser.add_argument("--collection", required=True, type=Path)
    parser.add_argument("--probe-url", action="append", default=[])
    args = parser.parse_args()
    if not args.collection.is_dir():
        parser.error("coleta não encontrada")
    result = generate(args.collection, args.probe_url)
    print(json.dumps({"ok": result["status"] == "GO", "status": result["status"], **result["summary"]}, ensure_ascii=False))
    return 0 if result["status"] == "GO" else 1


if __name__ == "__main__":
    raise SystemExit(main())
