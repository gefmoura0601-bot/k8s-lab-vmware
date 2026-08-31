#!/usr/bin/env python3
"""Interactive, server-rendered dashboard for read-only EKS assessments."""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import math
import os
import re
import secrets
import signal
import subprocess
import sys
import threading
from collections import Counter, defaultdict
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote_plus, urlencode, urlparse

from assessment_process_supervisor import CollectionSupervisor
from cis_security_assessment import compare_reports
from eks_comprehensive_assessment import sanitize_snapshot_tree
from localization_pt_br import localize_finding

LOCK = threading.Lock()
SUPERVISOR = CollectionSupervisor()
ACTION_TOKEN = secrets.token_urlsafe(32)
PLACEHOLDERS = {"", "cluster", "not-detected", "eks-production"}
WORKLOAD_KINDS = {"Deployment", "StatefulSet", "DaemonSet", "Rollout", "Job", "CronJob"}
SEVERITY_ORDER = {"CRIT": 0, "WARN": 1, "UNKNOWN": 2, "PARTIAL": 3, "INFO": 4, "PASS": 5, "N/A": 6}


def jfile(path: Path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return fallback


def jtext(value: str, fallback):
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return fallback


def list_items(path: Path) -> list[dict]:
    value = jfile(path, {"items": []})
    return value.get("items", []) if isinstance(value, dict) else []


def tsv(path: Path) -> list[dict]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    if not lines:
        return []
    keys = lines[0].split("\t")
    return [dict(zip(keys, line.split("\t"))) for line in lines[1:] if line]


def run(args: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    timeout = kwargs.pop("timeout", 300)
    try:
        return subprocess.run(args, text=True, capture_output=True, timeout=timeout, check=False, **kwargs)
    except (OSError, subprocess.TimeoutExpired) as error:
        return subprocess.CompletedProcess(args, 124, "", str(error))


def cluster() -> tuple[str, str]:
    configured = os.environ.get("EKS_CLUSTER_NAME", "").strip()
    if configured:
        return configured, configured
    result = run(["kubectl", "config", "current-context"], timeout=15)
    context = result.stdout.strip() if result.returncode == 0 else "cluster"
    view = run(["kubectl", "config", "view", "--minify", "-o", "json"], timeout=15)
    config = jtext(view.stdout, {}) if view.returncode == 0 else {}
    cluster_ref = (((config.get("contexts") or [{}])[0].get("context") or {}).get("cluster", ""))
    source = cluster_ref if ":cluster/" in cluster_ref else context
    name = source.rsplit(":cluster/", 1)[-1] if ":cluster/" in source else source.rsplit("@", 1)[-1] if "@" in source else source
    return context or "cluster", name or "cluster"


def eks_cluster_name() -> str:
    configured = os.environ.get("EKS_CLUSTER_NAME", "").strip()
    if configured:
        return configured
    context_result = run(["kubectl", "config", "current-context"], timeout=15)
    view_result = run(["kubectl", "config", "view", "--minify", "-o", "json"], timeout=15)
    context = context_result.stdout.strip() if context_result.returncode == 0 else ""
    config = jtext(view_result.stdout, {}) if view_result.returncode == 0 else {}
    cluster_ref = (((config.get("contexts") or [{}])[0].get("context") or {}).get("cluster", ""))
    for candidate in (cluster_ref, context):
        match = re.search(r"arn:[^:]+:eks:[^:]+:\d{12}:cluster/([^\s]+)", candidate)
        if match:
            return match.group(1)
    return ""


def prometheus_url_suggestion() -> str:
    configured = os.environ.get("PROMETHEUS_URL", "").strip()
    if configured:
        return configured
    result = run(["kubectl", "get", "services", "--all-namespaces", "-o", "json", "--request-timeout=10s"], timeout=15)
    if result.returncode != 0:
        return ""
    candidates: list[tuple[int, str]] = []
    for service in jtext(result.stdout, {}).get("items", []):
        metadata_value = service.get("metadata") or {}
        spec = service.get("spec") or {}
        cluster_ip = spec.get("clusterIP")
        labels = metadata_value.get("labels") or {}
        identity = f"{metadata_value.get('name', '')} {labels.get('app.kubernetes.io/name', '')}"
        if not cluster_ip or cluster_ip == "None" or "prometheus" not in identity.lower():
            continue
        for port in spec.get("ports") or []:
            number = port.get("port")
            port_name = str(port.get("name") or "")
            if isinstance(number, int) and (number == 9090 or re.search(r"prometheus|web|http", port_name, re.I)):
                candidates.append((0 if number == 9090 else 1, f"http://{cluster_ip}:{number}"))
    return sorted(candidates)[0][1] if candidates else ""


def metadata(directory: Path) -> dict:
    value = jfile(directory / "metadata.json", jfile(directory / "menu-metadata.json", {}))
    if str(value.get("clusterName", "")).strip() in PLACEHOLDERS or not value.get("context"):
        context, detected = cluster()
        if str(value.get("clusterName", "")).strip() in PLACEHOLDERS:
            value["clusterName"] = detected
        value.setdefault("context", context)
    value.setdefault("createdAt", dt.datetime.fromtimestamp(directory.stat().st_mtime, dt.timezone.utc).isoformat())
    value.setdefault("baseline", value.get("phase") == "before")
    value.setdefault("completed", True)
    return value


def node_ready(node: dict) -> bool:
    return any(x.get("type") == "Ready" and x.get("status") == "True" for x in (node.get("status") or {}).get("conditions", []))


def pod_reason(pod: dict) -> str:
    for status in (pod.get("status") or {}).get("containerStatuses", []):
        state = status.get("state") or {}
        value = (state.get("waiting") or {}).get("reason") or (state.get("terminated") or {}).get("reason")
        if value:
            return value
    return (pod.get("status") or {}).get("phase", "Unknown")


def owner(item: dict) -> str:
    refs = (item.get("metadata") or {}).get("ownerReferences", [])
    return f"{refs[0].get('kind')}/{refs[0].get('name')}" if refs else "-"


def esc(value) -> str:
    return html.escape(str(value if value is not None else ""))


def finite_number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def parse_cpu_quantity(value) -> float | None:
    """Return a Kubernetes CPU quantity in cores."""
    text = str(value or "").strip()
    if not text or text.lower() in {"unset", "unset/mixed", "mixed", "n/a", "none", "-"}:
        return None
    match = re.fullmatch(r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))([num]?)", text)
    if not match:
        return None
    multipliers = {"": 1.0, "m": 1e-3, "u": 1e-6, "n": 1e-9}
    return float(match.group(1)) * multipliers[match.group(2)]


def parse_memory_quantity(value) -> float | None:
    """Return a Kubernetes memory quantity in bytes."""
    text = str(value or "").strip()
    if not text or text.lower() in {"unset", "unset/mixed", "mixed", "n/a", "none", "-"}:
        return None
    match = re.fullmatch(r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))((?:[EPTGMK]i)|[eptgmkEPTGMK])?", text)
    if not match:
        return None
    suffix = match.group(2) or ""
    binary = {"Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "Ti": 2**40, "Pi": 2**50, "Ei": 2**60}
    decimal = {"k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9, "T": 1e12, "P": 1e15, "E": 1e18, "m": 1e-3}
    multiplier = binary.get(suffix, decimal.get(suffix, 1.0))
    return float(match.group(1)) * multiplier


def metric_p95(metrics: dict, name: str) -> float | None:
    metric = metrics.get(name) or {}
    if metric.get("state") != "AVAILABLE":
        return None
    return finite_number(metric.get("p95"))


def ratio_percent(value: float | None, reference: float | None) -> float | None:
    if value is None or reference is None or reference <= 0:
        return None
    return 100.0 * value / reference


def human_cpu(value: float | None) -> str:
    if value is None:
        return "N/A"
    millicores = value * 1000.0
    if 0 < abs(millicores) < 1:
        return f"{millicores:.2f} mCPU"
    if abs(millicores) < 10:
        return f"{millicores:.1f} mCPU"
    return f"{millicores:.0f} mCPU"


def human_bytes(value: float | None) -> str:
    if value is None:
        return "N/A"
    if abs(value) >= 2**30:
        return f"{value / 2**30:.2f} GiB"
    if abs(value) >= 2**20:
        return f"{value / 2**20:.1f} MiB"
    if abs(value) >= 2**10:
        return f"{value / 2**10:.1f} KiB"
    return f"{value:.0f} B"


def percent_html(value: float | None, warn: float = 80.0, crit: float = 100.0) -> str:
    if value is None:
        return '<span class="pct na">N/A</span>'
    level = "crit" if value >= crit else "warn" if value >= warn else "ok"
    label = f"{value:.1f}%" if 0 < abs(value) < 10 else f"{value:.0f}%"
    width = min(100.0, max(0.0, value))
    return f'<div class="pct-wrap" title="{value:.2f}%"><span class="pct {level}">{label}</span><span class="mini-bar"><i class="{level}" style="width:{width:.1f}%"></i></span></div>'

def table(rows: list[dict], columns: list[tuple[str, str]], raw: set[str] | None = None) -> str:
    raw = raw or set()
    head = "".join(f"<th>{esc(label)}</th>" for _, label in columns)
    body = []
    for row in rows:
        body.append("<tr>" + "".join(f"<td>{row.get(key, '-') if key in raw else esc(row.get(key, '-'))}</td>" for key, _ in columns) + "</tr>")
    rendered = "".join(body) or f'<tr><td colspan="{len(columns)}">Nenhum item nesta coleta.</td></tr>'
    return f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{rendered}</tbody></table></div>'


def workload_status(item: dict) -> tuple[int, int, int]:
    spec, status = item.get("spec") or {}, item.get("status") or {}
    desired = spec.get("replicas")
    if desired is None:
        desired = status.get("desiredNumberScheduled", spec.get("parallelism", spec.get("completions", 1)))
    desired = int(desired or 0)
    ready = int(status.get("readyReplicas") or status.get("numberReady") or status.get("availableReplicas") or status.get("succeeded") or 0)
    available = int(status.get("availableReplicas") or status.get("numberAvailable") or ready)
    return desired, ready, available


def generic_rows(path: Path, kind: str, status_fn=None, detail_fn=None) -> list[dict]:
    rows = []
    for item in list_items(path):
        meta = item.get("metadata") or {}
        rows.append({"kind": item.get("kind", kind), "namespace": meta.get("namespace", "-"), "name": meta.get("name", ""), "status": status_fn(item) if status_fn else "Available", "ready": "-", "node": "-", "restarts": 0, "detail": detail_fn(item) if detail_fn else ""})
    return rows


def inventory(directory: Path) -> dict[str, list[dict]]:
    nodes = []
    for item in list_items(directory / "nodes.json"):
        meta, status = item.get("metadata") or {}, item.get("status") or {}
        labels = meta.get("labels") or {}
        roles = [key.split("/", 1)[-1] for key in labels if key.startswith("node-role.kubernetes.io/")]
        addresses = {x.get("type"): x.get("address") for x in status.get("addresses", [])}
        zone = labels.get("topology.kubernetes.io/zone", "-")
        nodes.append({"kind": "Node", "namespace": "-", "name": meta.get("name", ""), "status": "Ready" if node_ready(item) else "NotReady", "ready": node_ready(item), "node": "-", "restarts": 0, "detail": f"Role={','.join(roles) or 'worker'} | IP={addresses.get('InternalIP','-')} | Zone={zone} | Kubernetes={(status.get('nodeInfo') or {}).get('kubeletVersion','-')}"})
    pods = []
    for item in list_items(directory / "pods.json"):
        meta, spec, status = item.get("metadata") or {}, item.get("spec") or {}, item.get("status") or {}
        statuses = status.get("containerStatuses", [])
        ready = sum(1 for x in statuses if x.get("ready"))
        pods.append({"kind": "Pod", "namespace": meta.get("namespace", ""), "name": meta.get("name", ""), "status": pod_reason(item), "phase": status.get("phase", "Unknown"), "ready": f"{ready}/{len(statuses)}", "node": spec.get("nodeName", "-"), "restarts": sum(int(x.get("restartCount", 0)) for x in statuses), "detail": f"Owner={owner(item)} | PodIP={status.get('podIP','-')}"})
    workloads = []
    for path, forced_kind in ((directory / "workloads.json", None), (directory / "jobs.json", "Job"), (directory / "cronjobs.json", "CronJob"), (directory / "rollouts.json", "Rollout")):
        for item in list_items(path):
            meta = item.get("metadata") or {}
            kind = forced_kind or item.get("kind", "Workload")
            desired, ready, available = workload_status(item)
            workloads.append({"kind": kind, "namespace": meta.get("namespace", ""), "name": meta.get("name", ""), "status": "Ready" if kind == "CronJob" or ready >= desired else "Degraded", "ready": ready, "desired": desired, "node": "-", "restarts": 0, "detail": f"Ready={ready}/{desired} | Available={available}"})
    namespaces = generic_rows(directory / "namespaces.json", "Namespace", lambda x: (x.get("status") or {}).get("phase", "Unknown"), lambda x: f"PSS={((x.get('metadata') or {}).get('labels') or {}).get('pod-security.kubernetes.io/enforce','N/A')}")
    services = generic_rows(directory / "services.json", "Service", lambda x: (x.get("spec") or {}).get("type", "ClusterIP"), lambda x: f"ClusterIP={(x.get('spec') or {}).get('clusterIP','-')} | Ports={len((x.get('spec') or {}).get('ports',[]))}")
    pvcs = generic_rows(directory / "pvcs.json", "PVC", lambda x: (x.get("status") or {}).get("phase", "Unknown"), lambda x: f"StorageClass={(x.get('spec') or {}).get('storageClassName','-')} | Request={((x.get('spec') or {}).get('resources') or {}).get('requests',{}).get('storage','-')}")
    hpas = generic_rows(directory / "hpas.json", "HPA", lambda x: f"{(x.get('status') or {}).get('currentReplicas',0)}/{(x.get('status') or {}).get('desiredReplicas',0)}", lambda x: f"Target={((x.get('spec') or {}).get('scaleTargetRef') or {}).get('kind','')}/{((x.get('spec') or {}).get('scaleTargetRef') or {}).get('name','')} | min={(x.get('spec') or {}).get('minReplicas',1)} | max={(x.get('spec') or {}).get('maxReplicas','-')}")
    keda = generic_rows(directory / "keda-scaledobjects.json", "ScaledObject", lambda _x: "Configured", lambda x: f"Target={((x.get('spec') or {}).get('scaleTargetRef') or {}).get('kind','Deployment')}/{((x.get('spec') or {}).get('scaleTargetRef') or {}).get('name','')} | Triggers={len((x.get('spec') or {}).get('triggers',[]))}")
    vpas = generic_rows(directory / "vpas.json", "VPA", lambda x: ((x.get("spec") or {}).get("updatePolicy") or {}).get("updateMode", "Configured"), lambda x: f"Target={((x.get('spec') or {}).get('targetRef') or {}).get('kind','')}/{((x.get('spec') or {}).get('targetRef') or {}).get('name','')}")
    ingresses = generic_rows(directory / "ingresses.json", "Ingress", lambda x: "TLS" if (x.get("spec") or {}).get("tls") else "NoTLS", lambda x: f"Class={(x.get('spec') or {}).get('ingressClassName','-')} | Rules={len((x.get('spec') or {}).get('rules',[]))}")
    gateways = generic_rows(directory / "gateways.json", "Gateway", lambda _x: "Configured", lambda x: f"Class={(x.get('spec') or {}).get('gatewayClassName','-')} | Listeners={len((x.get('spec') or {}).get('listeners',[]))}")
    result = {"nodes": nodes, "pods": pods, "workloads": workloads, "deployments": [x for x in workloads if x["kind"] == "Deployment"], "statefulsets": [x for x in workloads if x["kind"] == "StatefulSet"], "daemonsets": [x for x in workloads if x["kind"] == "DaemonSet"], "jobs": [x for x in workloads if x["kind"] == "Job"], "cronjobs": [x for x in workloads if x["kind"] == "CronJob"], "rollouts": [x for x in workloads if x["kind"] == "Rollout"], "namespaces": namespaces, "services": services, "pvcs": pvcs, "hpas": hpas, "keda": keda, "vpas": vpas, "ingresses": ingresses, "gateways": gateways}
    result["rabbitmq"] = [x for x in workloads + pods + services + pvcs if "rabbit" in f"{x.get('namespace')} {x.get('name')} {x.get('detail')}".lower()]
    return result


def basic_findings(directory: Path, resources: dict[str, list[dict]]) -> list[dict]:
    findings = []
    for row in tsv(directory / "findings.tsv"):
        severity = "CRIT" if row.get("status") == "FAIL" else row.get("status", "WARN")
        findings.append({"severity": severity, "status": "ASSESSMENT", "category": row.get("area", "Assessment"), "check": row.get("item", ""), "namespace": "-", "workload": row.get("item", ""), "container": "-", "detail": row.get("evidence", ""), "recommendation": "Validar a evidência e registrar correção, owner ou justificativa.", "technology": "", "source": ""})
    for pod in resources["pods"]:
        ready = str(pod["ready"]).split("/")
        if pod["status"] not in ("Running", "Succeeded") or ready[0] != ready[-1]:
            severity = "CRIT" if pod["status"] in ("CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull") else "WARN"
            findings.append({"severity": severity, "status": "ATUAL", "category": "PodHealth", "check": "Pod state", "namespace": pod["namespace"], "workload": f"Pod/{pod['name']}", "container": "-", "detail": pod["status"], "recommendation": "Revisar eventos, probes, logs, recursos e dependências.", "technology": "", "source": ""})
    return findings


def details(directory: Path) -> dict:
    comprehensive = jfile(directory / "comprehensive-assessment.json", {})
    resources = inventory(directory)
    findings = comprehensive.get("findings") or basic_findings(directory, resources)
    findings = sorted(findings, key=lambda x: (SEVERITY_ORDER.get(x.get("severity", "INFO"), 9), x.get("category", ""), x.get("namespace", ""), x.get("workload", "")))
    findings = [localize_finding(item) for item in findings]
    phases = Counter(x.get("phase") for x in resources["pods"])
    rabbit = next((x for x in resources["statefulsets"] if "rabbit" in x["name"].lower()), None)
    scanner_summary = comprehensive.get("summary") or {}
    summary = {"nodes": len(resources["nodes"]), "readyNodes": sum(1 for x in resources["nodes"] if x["ready"]), "pods": len(resources["pods"]), "running": phases["Running"], "pending": phases["Pending"], "failed": phases["Failed"], "deployments": len(resources["deployments"]), "statefulsets": len(resources["statefulsets"]), "daemonsets": len(resources["daemonsets"]), "jobs": len(resources["jobs"]), "cronjobs": len(resources["cronjobs"]), "rollouts": len(resources["rollouts"]), "namespaces": len(resources["namespaces"]), "services": len(resources["services"]), "pvcs": len(resources["pvcs"]), "hpas": len(resources["hpas"]), "keda": len(resources["keda"]), "vpas": len(resources["vpas"]), "rabbitReady": rabbit["ready"] if rabbit else 0, "rabbitDesired": rabbit.get("desired", 0) if rabbit else 0, **scanner_summary}
    return {"id": directory.name, "metadata": metadata(directory), "summary": summary, "resources": resources, "findings": findings, "metrics": tsv(directory / "prometheus-baseline.tsv"), "telemetry": jfile(directory / "prometheus-telemetry.json", {"state": "DISABLED"}), "discovery": jfile(directory / "discovery" / "summary.json", None), "comprehensive": comprehensive, "awsEks": jfile(directory / "aws-eks-assessment.json", comprehensive.get("awsEks", {"state": "UNKNOWN"})), "cloudProvider": jfile(directory / "cloud-provider-assessment.json", comprehensive.get("cloudProvider", {"state": "N/A", "provider": "generic-kubernetes"})), "cisSecurity": jfile(directory / "cis-security-assessment.json", comprehensive.get("cisSecurity", {})), "operationalInsights": jfile(directory / "operational-insights.json", comprehensive.get("operationalInsights", {})), "technologies": comprehensive.get("technologies", []), "capacity": comprehensive.get("capacityRecommendations", []), "coverage": (comprehensive.get("collection") or {}).get("resources", {}), "universal": jfile(directory / "universal-inventory.json", {"resources": []})}


class Handler(BaseHTTPRequestHandler):
    root: Path
    static: Path
    repository: Path
    access_token: str = ""

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def security_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")

    def authenticated(self) -> bool:
        if not self.access_token:
            return True
        parsed = urlparse(self.path)
        supplied = parse_qs(parsed.query).get("access_token", [""])[0]
        if supplied and secrets.compare_digest(supplied, self.access_token):
            query = parse_qs(parsed.query, keep_blank_values=True)
            query.pop("access_token", None)
            target = parsed.path + (f"?{urlencode(query, doseq=True)}" if query else "")
            self.send_response(303)
            self.send_header("Location", target or "/")
            self.send_header("Set-Cookie", f"assessment_session={self.access_token}; Path=/; HttpOnly; SameSite=Strict")
            self.send_header("Cache-Control", "no-store")
            self.security_headers()
            self.end_headers()
            return False
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        session = cookie.get("assessment_session")
        if session and secrets.compare_digest(session.value, self.access_token):
            return True
        self.send_html("<!doctype html><html lang=\"pt-BR\"><meta charset=\"utf-8\"><title>Acesso negado</title><h1>Acesso negado</h1><p>Use a URL temporária exibida pelo menu do assessment.</p></html>", 401)
        return False

    def log_message(self, fmt, *args):
        sys.stdout.write(f"{self.address_string()} - {fmt % args}\n")

    def directories(self) -> list[Path]:
        return sorted((x for x in self.root.glob("eks-*") if x.is_dir()), key=lambda x: metadata(x).get("createdAt", ""), reverse=True)

    def selected(self, query: dict[str, list[str]]) -> Path | None:
        ident = query.get("collection", [""])[0]
        if ident and re.fullmatch(r"[A-Za-z0-9._-]+", ident) and (self.root / ident).is_dir():
            return self.root / ident
        values = self.directories()
        return values[0] if values else None

    def send_html(self, value: str, status: int = 200):
        data = value.encode("utf-8")
        self.send_response(status); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(data))); self.send_header("Cache-Control", "no-store"); self.security_headers(); self.end_headers(); self.wfile.write(data)

    def send_json(self, value, status: int = 200, filename: str | None = None):
        data = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status); self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Content-Length", str(len(data))); self.send_header("Cache-Control", "no-store"); self.security_headers()
        if filename: self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers(); self.wfile.write(data)

    def layout(self, title: str, body: str, directory: Path | None = None, active: str = "overview") -> str:
        value = metadata(directory) if directory else {"clusterName": cluster()[1]}
        ident = directory.name if directory else ""
        cq = urlencode({"collection": ident}) if ident else ""
        groups = [
            ("VISÃO", [("overview", "/", "Visão geral"), ("search", "/search", "Busca global")]),
            ("ANÁLISE", [("assessment", "/assessment", "Assessment"), ("problems", "/problems", "Problemas"), ("cis", "/cis-security", "CIS Security"), ("best", "/best-practices", "Best Practices")]),
            ("OPERAÇÕES", [("diagnostics", "/diagnostics", "Events & Diagnostics"), ("node-health", "/node-health", "Node Health"), ("versions", "/versions", "Versions & Lifecycle"), ("manifests", "/manifest-quality", "Manifest Quality"), ("logs", "/logs", "Logs"), ("capacity", "/capacity", "Container Tuning")]),
            ("INVENTÁRIO", [("nodes", "/resources?kind=nodes", "Nodes"), ("namespaces", "/resources?kind=namespaces", "Namespaces"), ("workloads", "/resources?kind=workloads", "Workloads"), ("technologies", "/technologies", "Tecnologias"), ("rabbitmq", "/resources?kind=rabbitmq", "RabbitMQ")]),
            ("INTEGRAÇÕES", [("prometheus", "/prometheus", "Prometheus"), ("cloud", "/cloud", "Cloud Provider"), ("aws", "/aws", "AWS / EKS detalhado"), ("coverage", "/coverage", "Cobertura")]),
            ("RELATÓRIOS", [("compare", "/compare", "Comparar coletas")]),
        ]
        nav = []
        for group_label, links in groups:
            group_active = any(key == active for key, _, _ in links)
            items = []
            for key, path, label in links:
                separator = "&" if "?" in path else "?"
                href = path + (separator + cq if cq else "")
                items.append(f'<a class="tab {"active" if key == active else ""}" href="{href}">{esc(label)}</a>')
            nav.append(f'<details class="nav-group" {"open" if group_active else ""}><summary>{esc(group_label)}</summary>{"".join(items)}</details>')
        global_search = f'<form class="global-search" action="/search" method="get"><input type="hidden" name="collection" value="{esc(ident)}"><label for="global-q">BUSCA GLOBAL</label><div><input id="global-q" name="q" minlength="2" placeholder="Recurso, Rule ID, Event..."><button aria-label="Pesquisar">⌕</button></div></form>'
        options = "".join(f'<option value="{esc(x.name)}" {"selected" if directory and x == directory else ""}>{esc(x.name)}{" • baseline" if metadata(x).get("baseline") else ""}</option>' for x in self.directories()) or '<option>Nenhuma coleta</option>'
        picker = f'<form class="picker" method="get"><label>Coleta<select name="collection">{options}</select></label><button>Carregar</button></form>'
        control = SUPERVISOR.status()
        actions = f'<a class="button" href="/?{cq}">Atualizar tela</a><a class="button" href="/collect">Coletar agora</a><a class="button" href="/collect?baseline=1">Novo baseline</a>'
        if control.get("active"):
            actions += f'<form class="inline-action" method="post" action="/cancel"><input type="hidden" name="action_token" value="{ACTION_TOKEN}"><button class="button danger" type="submit">Cancelar coleta</button></form>'
        if directory: actions += f'<a class="button" href="/export?{cq}">Exportar</a>'
        return f'<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="theme-color" content="#173b73"><title>{esc(title)} · Kubernetes Assessment</title><link rel="icon" href="/kubernetes-logo.svg" type="image/svg+xml"><link rel="stylesheet" href="/styles.css"></head><body><header><div class="environment"><span class="status-dot" aria-hidden="true"></span>KUBERNETES ASSESSMENT <span>{esc(value.get("clusterName"))}</span></div><div class="top"><div class="brand"><img src="/kubernetes-logo.svg" alt="Kubernetes"><div><strong>Kubernetes</strong><em>ASSESSMENT CONSOLE</em></div></div><span class="health">READ-ONLY</span><span class="headline">{esc(ident or "sem coleta")}</span><div class="actions">{actions}</div></div></header><main><aside><div class="nav-title">NAVEGAÇÃO</div>{global_search}<nav aria-label="Navegação principal">{"".join(nav)}</nav></aside><section class="content">{picker}{body}</section></main></body></html>'

    def overview(self, directory: Path | None) -> str:
        if not directory: return self.layout("EKS Assessment", '<div class="message">Nenhuma coleta disponível. Use Coletar agora.</div>')
        value, ident = details(directory), esc(directory.name)
        summary, findings = value["summary"], value["findings"]
        critical = sum(x.get("severity") == "CRIT" for x in findings); warnings = sum(x.get("severity") == "WARN" for x in findings)
        unknown = sum(x.get("severity") == "UNKNOWN" for x in findings); partial = sum(x.get("severity") == "PARTIAL" for x in findings)
        state = "CRÍTICO" if critical else "ATENÇÃO" if warnings else "EVIDÊNCIA INCOMPLETA" if unknown or partial else "OK"
        cards = [("NODES", summary["nodes"], "nodes"), ("NODES READY", summary["readyNodes"], "nodes"), ("PODS", summary["pods"], "pods"), ("RUNNING", summary["running"], "pods&status=Running"), ("PENDING", summary["pending"], "pods&status=Pending"), ("FAILED", summary["failed"], "pods&status=Failed"), ("DEPLOYMENTS", summary["deployments"], "deployments"), ("STATEFULSETS", summary["statefulsets"], "statefulsets"), ("DAEMONSETS", summary["daemonsets"], "daemonsets"), ("JOBS", summary["jobs"], "jobs"), ("CRONJOBS", summary["cronjobs"], "cronjobs"), ("ROLLOUTS", summary["rollouts"], "rollouts"), ("HPA", summary["hpas"], "hpas"), ("KEDA", summary["keda"], "keda"), ("PVC", summary["pvcs"], "pvcs"), ("RABBITMQ", f'{summary["rabbitReady"]}/{summary["rabbitDesired"]}', "rabbitmq")]
        card_html = "".join(f'<a class="card" href="/resources?collection={ident}&kind={kind}"><small>{label}</small><b>{count}</b></a>' for label, count, kind in cards)
        discovery = value.get("discovery") or {}; scanner = value.get("comprehensive", {}).get("summary", {})
        node_health = (value.get("operationalInsights") or {}).get("nodeHealth") or {}
        node_health_state = node_health.get("state", "EVIDENCE_UNAVAILABLE")
        card_html += f'<a class="card" href="/node-health?collection={ident}"><small>NODE HEALTH</small><b>{esc(node_health_state)}</b></a>'
        priority = table(findings[:20], [("severity", "Severidade"), ("category", "Categoria"), ("namespace", "Namespace"), ("workload", "Workload"), ("check", "Check"), ("detail", "Evidência"), ("recommendation", "Recomendação")])
        state_css = "critical" if critical else "warning" if warnings else "incomplete" if unknown or partial else "healthy"
        if critical:
            explanation = f'Estado CRÍTICO porque {critical} finding(s) de severidade CRIT exigem priorização. Isso indica riscos relevantes; não significa necessariamente indisponibilidade total do cluster.'
            action = f'<a class="state-action" href="/problems?collection={ident}&severity=CRIT">Ver findings críticos</a>'
        elif warnings:
            explanation = f'Estado ATENÇÃO porque existem {warnings} finding(s) WARN sem findings CRIT nesta coleta.'
            action = f'<a class="state-action" href="/problems?collection={ident}&severity=WARN">Ver alertas</a>'
        elif unknown or partial:
            explanation = 'A avaliação possui evidência incompleta. Ausência de evidência nunca é tratada como conformidade.'
            action = f'<a class="state-action" href="/coverage?collection={ident}">Ver cobertura</a>'
        else:
            explanation = 'Nenhum finding CRIT ou WARN foi identificado com a evidência disponível nesta coleta.'
            action = f'<a class="state-action" href="/assessment?collection={ident}">Abrir assessment</a>'
        drivers = Counter(x.get("category", "Assessment") for x in findings if x.get("severity") == ("CRIT" if critical else "WARN"))
        driver_text = ", ".join(f'{name} ({count})' for name, count in drivers.most_common(4))
        driver_html = f'<p class="state-drivers"><strong>Principais fatores:</strong> {esc(driver_text)}</p>' if driver_text else ''
        counts_html = f'<div class="state-counts"><span class="crit">{critical} CRIT</span><span class="warn">{warnings} WARN</span><span class="unknown">{unknown} UNKNOWN</span><span class="partial">{partial} PARTIAL</span><span class="pass">{scanner.get("passed",0)} PASS</span><span>{scanner.get("notApplicable",0)} N/A</span></div>'
        state_actions = f'<div class="state-actions">{action}<a class="state-action secondary" href="/node-health?collection={ident}">Abrir Node Health</a></div>'
        body = f'<section class="state {state_css}"><small>SAÚDE DO AMBIENTE</small><h1>{state}</h1><p class="state-explanation">{esc(explanation)}</p>{driver_html}{counts_html}<p class="state-scope">{scanner.get("checks","N/A")} checks em {scanner.get("workloads","N/A")} workloads / {scanner.get("containers","N/A")} containers. Discovery {discovery.get("succeeded","N/A")}/{discovery.get("sections","N/A")}.</p>{state_actions}</section><div class="cards">{card_html}</div><h2>Problemas e recomendações prioritárias</h2>{priority}'
        return self.layout("Visão geral", body, directory, "overview")

    def search_page(self, directory: Path | None, query: dict[str, list[str]]) -> str:
        if not directory:
            return self.overview(None)
        term = query.get("q", [""])[0].strip()
        form = f'<form class="filters global-results" action="/search"><input type="hidden" name="collection" value="{esc(directory.name)}"><input name="q" minlength="2" value="{esc(term)}" placeholder="Recurso, namespace, Rule ID, Event, versão ou recomendação"><button>Pesquisar</button></form>'
        if len(term) < 2:
            return self.layout("Busca global", f'<h1>Busca global</h1><p>Pesquise em findings, inventário, CIS Security, Events, Versions, Manifest Quality e Best Practices.</p>{form}<div class="message">Informe ao menos dois caracteres.</div>', directory, "search")
        value = details(directory)
        needle = term.lower()
        collection = quote_plus(directory.name)
        rows: list[dict] = []

        def add(source: str, title: Any, status: Any, detail: Any, href: str, payload: Any) -> None:
            if needle not in json.dumps(payload, ensure_ascii=False, default=str).lower() or len(rows) >= 500:
                return
            rendered = str(detail or "-")
            if len(rendered) > 500:
                rendered = rendered[:497] + "..."
            rows.append({"source": source, "title": title or "-", "status": status or "-", "detail": rendered, "open": f'<a class="resource-link" href="{esc(href)}">Abrir</a>'})

        for finding in value.get("findings") or []:
            add("Finding", finding.get("check") or finding.get("ruleId"), finding.get("severity"), finding.get("detail"), f'/problems?collection={collection}&search={quote_plus(str(finding.get("ruleId") or finding.get("check") or ""))}', finding)
        resource_kinds = ("nodes", "namespaces", "workloads", "services", "pvcs", "hpas", "keda", "vpas", "ingresses", "gateways", "rabbitmq")
        seen: set[tuple[str, str, str]] = set()
        for kind in resource_kinds:
            for item in (value.get("resources") or {}).get(kind) or []:
                identity = (kind, str(item.get("namespace") or "-"), str(item.get("name") or item.get("ref") or "-"))
                if identity in seen:
                    continue
                seen.add(identity)
                add("Inventory", identity[2], item.get("status") or item.get("ready") or "DETECTED", item.get("detail") or identity[1], f'/resources?collection={collection}&kind={quote_plus(kind)}&search={quote_plus(term)}', item)
        cis = value.get("cisSecurity") or {}
        for item in cis.get("controls") or []:
            add("CIS Security", item.get("controlId"), item.get("status"), item.get("recommendation"), f'/cis-security?collection={collection}&search={quote_plus(term)}', item)
        operational = value.get("operationalInsights") or {}
        sections = [
            ("Event", ((operational.get("diagnostics") or {}).get("events") or []), "/diagnostics", "reason", "type", "recommendation"),
            ("Node Health", ((operational.get("nodeHealth") or {}).get("items") or []), "/node-health", "node", "state", "diagnosis"),
            ("Version", ((operational.get("versions") or {}).get("items") or []), "/versions", "name", "supportState", "version"),
            ("Manifest Quality", ((operational.get("manifestQuality") or {}).get("findings") or []), "/manifest-quality", "check", "severity", "evidence"),
            ("Best Practice", ((operational.get("bestPractices") or {}).get("rules") or []), "/best-practices", "ruleId", "status", "recommendation"),
        ]
        for source, items_list, route, title_key, status_key, detail_key in sections:
            for item in items_list:
                add(source, item.get(title_key), item.get(status_key), item.get(detail_key), f'{route}?collection={collection}', item)
        for item in ((operational.get("logs") or {}).get("entries") or []):
            safe_log_index = {"target": item.get("target"), "state": item.get("state"), "reason": item.get("reason")}
            add("Log", item.get("target"), item.get("state"), item.get("reason") or "Conteúdo sanitizado disponível na aba Logs.", f'/logs?collection={collection}', safe_log_index)
        message = f'<div class="message good">{len(rows)} resultado(s) para <b>{esc(term)}</b>. Limite: 500; conteúdo de logs não é indexado.</div>' if rows else f'<div class="message">Nenhum resultado para <b>{esc(term)}</b>.</div>'
        body = f'<h1>Busca global</h1>{form}{message}{table(rows, [("source","Origem"),("title","Item"),("status","Estado"),("detail","Detalhe"),("open","Ação")], raw={"open"})}'
        return self.layout("Busca global", body, directory, "search")

    def resources_page(self, directory: Path | None, query: dict[str, list[str]]) -> str:
        if not directory: return self.overview(None)
        value = details(directory); kind = query.get("kind", ["workloads"])[0]
        all_rows = list(value["resources"].get(kind, [])); rows = all_rows
        status = query.get("status", [""])[0]; search = query.get("search", [""])[0].lower(); namespace = query.get("namespace", [""])[0]
        if status: rows = [x for x in rows if x.get("phase") == status or x.get("status") == status]
        if search: rows = [x for x in rows if search in json.dumps(x, ensure_ascii=False).lower()]
        if namespace: rows = [x for x in rows if x.get("namespace") == namespace]
        namespaces = sorted({x.get("namespace") for x in all_rows if x.get("namespace") not in (None, "-")})
        options = "".join(f'<option value="{esc(x)}" {"selected" if x == namespace else ""}>{esc(x)}</option>' for x in namespaces)
        filters = f'<form class="filters"><input type="hidden" name="collection" value="{esc(directory.name)}"><input type="hidden" name="kind" value="{esc(kind)}"><input name="search" value="{esc(query.get("search",[""])[0])}" placeholder="Filtrar nome, namespace, status ou detalhe"><select name="namespace"><option value="">Todos os namespaces</option>{options}</select><button>Filtrar</button></form>'
        node_health_map = {
            str(item.get("node")): item
            for item in (((value.get("operationalInsights") or {}).get("nodeHealth") or {}).get("items") or [])
        }
        rendered = []
        for row in rows:
            copy = dict(row)
            if row.get("kind") in WORKLOAD_KINDS:
                params = urlencode({"collection": directory.name, "namespace": row.get("namespace", ""), "kind": row.get("kind", ""), "name": row.get("name", "")})
                copy["nameHtml"] = f'<a class="resource-link" href="/workload?{params}">{esc(row.get("name"))}</a>'
            else: copy["nameHtml"] = esc(row.get("name"))
            if kind == "nodes":
                health = node_health_map.get(str(row.get("name"))) or {}
                health_state = str(health.get("state") or "EVIDENCE_UNAVAILABLE")
                css = {"CRIT": "crit", "WARN": "warn", "PARTIAL": "info", "PASS": "ok"}.get(health_state, "na")
                health_href = urlencode({"collection": directory.name})
                copy["healthHtml"] = f'<a href="/node-health?{health_href}"><span class="metric-status {css}">{esc(health_state)}</span></a>'
                health_usage = health.get("usage") or {}
                copy["cpuHtml"] = percent_html(finite_number((health_usage.get("cpu") or {}).get("percent")), 85, 95)
                copy["memoryHtml"] = percent_html(finite_number((health_usage.get("memory") or {}).get("percent")), 80, 90)
                copy["podsHtml"] = percent_html(finite_number((health_usage.get("pods") or {}).get("percent")), 80, 95)
            rendered.append(copy)
        if kind == "nodes":
            columns = [("kind", "Kind"), ("nameHtml", "Nome"), ("status", "Status"), ("healthHtml", "Node Health"), ("cpuHtml", "CPU em uso"), ("memoryHtml", "Memória em uso"), ("podsHtml", "Densidade de Pods"), ("detail", "Detalhe")]
            raw_columns = {"nameHtml", "healthHtml", "cpuHtml", "memoryHtml", "podsHtml"}
        else:
            columns = [("kind", "Kind"), ("namespace", "Namespace"), ("nameHtml", "Nome"), ("status", "Status"), ("ready", "Ready"), ("node", "Node"), ("restarts", "Restarts"), ("detail", "Detalhe")]
            raw_columns = {"nameHtml"}
        active = "rabbitmq" if kind == "rabbitmq" else "nodes" if kind == "nodes" else "namespaces" if kind == "namespaces" else "workloads"
        return self.layout(kind.upper(), f'<h1>{esc(kind.upper())} <small>{len(rows)} item(ns)</small></h1>{filters}{table(rendered, columns, raw_columns)}', directory, active)

    def problems(self, directory: Path | None, query: dict[str, list[str]]) -> str:
        if not directory: return self.overview(None)
        value = details(directory); rows = value["findings"]
        severity = query.get("severity", [""])[0]; category = query.get("category", [""])[0]; search = query.get("search", [""])[0].lower()
        if severity: rows = [x for x in rows if x.get("severity") == severity]
        if category: rows = [x for x in rows if x.get("category") == category]
        if search: rows = [x for x in rows if search in json.dumps(x, ensure_ascii=False).lower()]
        severities = "".join(f'<option value="{x}" {"selected" if severity == x else ""}>{x}</option>' for x in ("CRIT", "WARN", "UNKNOWN", "PARTIAL", "INFO", "PASS", "N/A"))
        categories = sorted({x.get("category", "") for x in value["findings"]}); category_options = "".join(f'<option value="{esc(x)}" {"selected" if category == x else ""}>{esc(x)}</option>' for x in categories)
        filters = f'<form class="filters"><input type="hidden" name="collection" value="{esc(directory.name)}"><select name="severity"><option value="">Todas as severidades</option>{severities}</select><select name="category"><option value="">Todas as categorias</option>{category_options}</select><input name="search" value="{esc(query.get("search",[""])[0])}" placeholder="Filtrar namespace, workload, container, evidência ou tecnologia"><button>Filtrar</button></form>'
        rendered = []
        for row in rows:
            copy = dict(row); source = row.get("source", "")
            copy["sourceHtml"] = f'<a href="{esc(source)}" target="_blank" rel="noreferrer">Referência</a>' if source.startswith("https://") else "-"; rendered.append(copy)
        columns = [("severity", "Severidade"), ("status", "Status"), ("category", "Categoria"), ("namespace", "Namespace"), ("workload", "Workload"), ("container", "Container"), ("check", "Check"), ("detail", "Evidência"), ("recommendation", "Recomendação"), ("sourceHtml", "Fonte")]
        return self.layout("Problemas", f'<h1>Problemas, evidências e recomendações <small>{len(rows)} resultado(s)</small></h1>{filters}{table(rendered, columns, {"sourceHtml"})}', directory, "problems")

    def assessment(self, directory: Path | None) -> str:
        if not directory: return self.overview(None)
        groups = defaultdict(list)
        for item in details(directory)["findings"]: groups[item.get("category", "Assessment")].append(item)
        cards = []
        for category, rows in sorted(groups.items()):
            severities = {x.get("severity") for x in rows}; level = next((x for x in ("CRIT", "WARN", "UNKNOWN", "PARTIAL", "INFO", "PASS", "N/A") if x in severities), "N/A"); counts = Counter(x.get("severity") for x in rows)
            href = f'/problems?collection={esc(directory.name)}&category={quote_plus(category)}'
            cards.append(f'<a class="assessment-card {level.replace("/", "")}" href="{href}"><span class="badge">{level}</span><h2>{esc(category)}</h2><p>{len(rows)} check(s): {counts["CRIT"]} CRIT · {counts["WARN"]} WARN · {counts["UNKNOWN"]} UNKNOWN · {counts["PARTIAL"]} PARTIAL · {counts["PASS"]} PASS · {counts["N/A"]} N/A</p><small>{esc(rows[0].get("detail",""))}</small></a>')
        return self.layout("Assessment", f'<h1>Assessment adaptativo de melhores práticas</h1><div class="assessment-cards">{"".join(cards) or "Sem achados"}</div>', directory, "assessment")

    def workload(self, directory: Path | None, query: dict[str, list[str]]) -> str:
        if not directory: return self.overview(None)
        namespace, kind, name = (query.get(x, [""])[0] for x in ("namespace", "kind", "name")); value = details(directory)
        workload = next((x for x in (value["comprehensive"].get("workloads") or []) if x.get("namespace") == namespace and x.get("kind") == kind and x.get("name") == name), None)
        if not workload: return self.layout("Workload", '<div class="message bad">Detalhe do workload não encontrado na análise abrangente.</div>', directory, "workloads")
        containers = []
        for item in workload.get("containers", []):
            copy = dict(item); copy["technologies"] = ", ".join(item.get("technologies") or []) or "-"; copy["runtimeOptions"] = json.dumps(item.get("runtimeOptions") or {}, ensure_ascii=False); copy["requests"] = json.dumps(item.get("requests") or {}, ensure_ascii=False); copy["limits"] = json.dumps(item.get("limits") or {}, ensure_ascii=False); containers.append(copy)
        ref = f"{kind}/{name}"; findings = [x for x in value["findings"] if x.get("namespace") == namespace and x.get("workload") == ref]
        summary = f'<div class="facts"><div><small>Réplicas</small><b>{workload.get("readyReplicas")}/{workload.get("replicas")}</b></div><div><small>Autoscaling</small><b>{esc(workload.get("autoscaling"))}</b></div><div><small>PDB</small><b>{esc(workload.get("pdb") or "ausente")}</b></div><div><small>Topology spread</small><b>{workload.get("topologySpreadConstraints",0)}</b></div><div><small>NetworkPolicies no namespace</small><b>{workload.get("networkPolicyObjectsInNamespace",0)}</b></div></div>'
        container_table = table(containers, [("type", "Tipo"), ("name", "Container"), ("image", "Imagem"), ("technologies", "Tecnologias"), ("requests", "Requests"), ("limits", "Limits"), ("runtimeOptions", "Opções de runtime")])
        finding_table = table(findings, [("severity", "Severidade"), ("category", "Categoria"), ("container", "Container"), ("check", "Check"), ("detail", "Evidência"), ("recommendation", "Recomendação")])
        return self.layout(ref, f'<h1>{esc(ref)} <small>{esc(namespace)}</small></h1>{summary}<h2>Containers e configuração sanitizada</h2>{container_table}<h2>Checks e recomendações</h2>{finding_table}', directory, "workloads")

    def technologies(self, directory: Path | None) -> str:
        if not directory: return self.overview(None)
        technologies = details(directory)["technologies"]; cards = []; rows = []
        for item in technologies:
            state = item.get("state", "N/A"); refs = item.get("workloads") or []; href = f'/problems?collection={esc(directory.name)}&search={quote_plus(item.get("name",""))}'
            css = "PASS" if state == "DETECTED" else "NA"; cards.append(f'<a class="assessment-card {css}" href="{href}"><span class="badge">{esc(state)}</span><h2>{esc(item.get("name"))}</h2><p>{len(refs)} container(s)</p></a>')
            rows.extend({"technology": item.get("name"), "workload": ref} for ref in refs)
        return self.layout("Tecnologias", f'<h1>Tecnologias descobertas</h1><p>Detecção baseada em imagem, nome, comando e variáveis de tuning permitidas; deve ser confirmada por SBOM/runtime.</p><div class="assessment-cards">{"".join(cards)}</div><h2>Mapeamento</h2>{table(rows,[("technology","Tecnologia"),("workload","Workload / container")])}', directory, "technologies")

    def diagnostics(self, directory: Path | None) -> str:
        if not directory: return self.overview(None)
        value = (details(directory).get("operationalInsights") or {}).get("diagnostics") or {}
        summary = value.get("summary") or {}
        facts = f'<div class="facts"><div><small>Grupos de Events</small><b>{summary.get("groups",0)}</b></div><div><small>Warnings</small><b>{summary.get("warnings",0)}</b></div><div><small>Pods afetados</small><b>{summary.get("affectedPods",0)}</b></div></div>'
        events = table(value.get("events") or [], [("type","Tipo"),("reason","Reason"),("namespace","Namespace"),("kind","Kind"),("resource","Recurso"),("count","Ocorrências"),("lastSeen","Último evento"),("recommendation","Recomendação")])
        pods = [{**x, "reasons": ", ".join(x.get("reasons") or [])} for x in value.get("podStates") or []]
        return self.layout("Events & Diagnostics", f'<h1>Events & Diagnostics</h1><p>Events deduplicados e correlacionados com estado de Pods. Mensagens livres não são persistidas.</p>{facts}<h2>Events</h2>{events}<h2>Pods com sinais operacionais</h2>{table(pods,[("namespace","Namespace"),("pod","Pod"),("phase","Phase"),("restarts","Restarts"),("reasons","Reasons"),("node","Node")])}', directory, "diagnostics")

    def node_health(self, directory: Path | None) -> str:
        if not directory: return self.overview(None)
        value = (details(directory).get("operationalInsights") or {}).get("nodeHealth") or {}
        summary = value.get("summary") or {}
        facts = (
            '<div class="facts">'
            f'<div><small>Estado</small><b>{esc(value.get("state", "EVIDENCE_UNAVAILABLE"))}</b></div>'
            f'<div><small>Nodes avaliados</small><b>{summary.get("nodes", 0)}</b></div>'
            f'<div><small>CRIT / WARN</small><b>{summary.get("critical", 0)} / {summary.get("warnings", 0)}</b></div>'
            f'<div><small>Metrics API</small><b>{summary.get("metricsNodes", 0)}/{summary.get("nodes", 0)}</b></div>'
            f'<div><small>Cobertura de uso</small><b>{summary.get("metricsCoveragePercent", 0):.0f}%</b></div>'
            '</div>'
        )

        def composition(item: dict, resource: str) -> str:
            usage = item.get("usage") or {}
            breakdown = usage.get("breakdown") or {}
            allocatable = item.get("allocatable") or {}
            key = "cpuCores" if resource == "cpu" else "memoryBytes"
            reference = finite_number(allocatable.get(key))
            formatter = human_cpu if resource == "cpu" else human_bytes
            parts = (
                ("kubernetesPods", "Kubernetes/System Pods", "system"),
                ("daemonSets", "DaemonSets", "daemon"),
                ("workloads", "Application workloads", "workload"),
                ("nodeOverheadUnattributed", "Node overhead / não atribuído", "overhead"),
                ("headroom", "Headroom", "headroom"),
            )
            segments, legend = [], []
            for name, label, css in parts:
                value_number = finite_number((breakdown.get(name) or {}).get(key))
                width = min(100.0, max(0.0, ratio_percent(value_number, reference) or 0.0))
                if value_number is not None and width > 0:
                    segments.append(f'<span class="{css}" style="width:{width:.2f}%" title="{esc(label)}: {esc(formatter(value_number))}"></span>')
                legend.append(f'<span><i class="{css}"></i>{esc(label)} <b>{esc(formatter(value_number))}</b></span>')
            if not segments:
                return '<div class="composition-unavailable">EVIDENCE_UNAVAILABLE</div>'
            return f'<div class="composition-bar">{"".join(segments)}</div><div class="composition-legend">{"".join(legend)}</div>'

        cards = []
        css_by_state = {"CRIT": "crit", "WARN": "warn", "PARTIAL": "info", "PASS": "ok"}
        for item in value.get("items") or []:
            state = str(item.get("state") or "PARTIAL")
            css = css_by_state.get(state, "na")
            usage = item.get("usage") or {}
            cpu, memory, pods = usage.get("cpu") or {}, usage.get("memory") or {}, usage.get("pods") or {}
            requests = usage.get("requests") or {}
            reserve = item.get("nodeReserve") or {}
            evidence = item.get("evidence") or {}
            diagnostics = "; ".join(str(x) for x in item.get("diagnosis") or [])
            pressure = ", ".join(item.get("pressureConditions") or []) or "nenhuma"
            cpu_request_percent = finite_number(requests.get("cpuPercent"))
            memory_request_percent = finite_number(requests.get("memoryPercent"))
            cpu_request_label = f"{cpu_request_percent:.1f}%" if cpu_request_percent is not None else "N/A"
            memory_request_label = f"{memory_request_percent:.1f}%" if memory_request_percent is not None else "N/A"
            ready_label = "sim" if item.get("ready") is True else "não" if item.get("ready") is False else "UNKNOWN"
            cards.append(
                f'<article class="node-health-card {css}"><header><div><small>NODE</small><h2>{esc(item.get("node"))}</h2></div><span class="metric-status {css}">{esc(state)}</span></header>'
                f'<div class="node-health-facts"><span><small>Ready</small><b>{ready_label}</b></span><span><small>CPU em uso</small><b>{esc(human_cpu(finite_number(cpu.get("value"))))}</b>{percent_html(finite_number(cpu.get("percent")), 85, 95)}</span><span><small>Memória em uso</small><b>{esc(human_bytes(finite_number(memory.get("value"))))}</b>{percent_html(finite_number(memory.get("percent")), 80, 90)}</span><span><small>Pods</small><b>{pods.get("value", 0)}/{(item.get("allocatable") or {}).get("pods", "N/A")}</b>{percent_html(finite_number(pods.get("percent")), 80, 95)}</span></div>'
                f'<h3>Decomposição observada de CPU</h3>{composition(item, "cpu")}<h3>Decomposição observada de memória</h3>{composition(item, "memory")}'
                f'<details class="node-health-details"><summary>Capacidade, reserva e evidência</summary><p><b>Requests:</b> CPU {esc(human_cpu(finite_number(requests.get("cpuCores"))))} ({esc(cpu_request_label)}); memória {esc(human_bytes(finite_number(requests.get("memoryBytes"))))} ({esc(memory_request_label)}).</p><p><b>Reserva do node:</b> CPU {esc(human_cpu(finite_number(reserve.get("cpuCores"))))}; memória {esc(human_bytes(finite_number(reserve.get("memoryBytes"))))}. Reserva é capacity menos allocatable, não uso real.</p><p><b>Runtime:</b> {esc(item.get("runtime"))} · <b>OS:</b> {esc(item.get("os"))}</p><p><b>Pressão:</b> {esc(pressure)} · <b>Evidence Source:</b> KubernetesAPI + {esc(evidence.get("metrics"))} · <b>Pod metrics:</b> {evidence.get("runningPodsObserved", 0)}/{evidence.get("runningPodsExpected", 0)}.</p></details>'
                f'<p class="node-diagnosis"><strong>Diagnóstico:</strong> {esc(diagnostics)}</p></article>'
            )
        content = "".join(cards) or '<div class="message">Node Health indisponível nesta coleta. Execute uma nova coleta com acesso read-only a nodes e metrics.k8s.io.</div>'
        notice = f'<div class="metric-legend">{esc(value.get("notice") or "Esta coleta não possui evidência de Node Health.")}</div>'
        return self.layout("Node Health", f'<h1>Node Health</h1><p>Saúde provider-neutral para on-premises, EKS, AKS e GKE, sem SSH ou acesso ao filesystem do node.</p>{notice}{facts}<div class="node-health-grid">{content}</div>', directory, "node-health")

    def versions(self, directory: Path | None) -> str:
        if not directory: return self.overview(None)
        value = (details(directory).get("operationalInsights") or {}).get("versions") or {}
        summary = value.get("summary") or {}
        catalog = value.get("catalog") or {}
        catalog_state = "DESATUALIZADO" if catalog.get("stale") else "ATUAL"
        message = f'<div class="message warn">{esc(value.get("notice",""))} Componentes: {summary.get("components",0)}; versões desconhecidas: {summary.get("unknownVersions",0)}; lifecycle sem evidência: {summary.get("lifecycleEvidenceUnavailable",0)}; fim de suporte: {summary.get("endOfSupport",0)}; version skew nos nodes: {"sim" if summary.get("nodeVersionSkew") else "não"}. Catálogo: {esc(catalog.get("asOf","UNKNOWN"))} ({catalog_state}).</div>'
        columns = [("component","Componente"),("name","Nome"),("version","Versão"),("supportState","Support State"),("supportUntil","Support Until"),("daysRemaining","Dias restantes"),("lifecycleReason","Motivo"),("runtime","Runtime"),("os","Sistema operacional"),("kernel","Kernel"),("state","Estado"),("source","Evidence Source")]
        return self.layout("Versions & Lifecycle", f'<h1>Versions & Lifecycle</h1>{message}{table(value.get("items") or [],columns)}', directory, "versions")

    def manifest_quality(self, directory: Path | None) -> str:
        if not directory: return self.overview(None)
        value = (details(directory).get("operationalInsights") or {}).get("manifestQuality") or {}
        summary = value.get("summary") or {}
        message = f'<div class="message">{esc(value.get("notice",""))} Recursos: {summary.get("resources",0)}; issues: {summary.get("issues",0)}.</div>'
        columns = [("severity","Severidade"),("category","Categoria"),("namespace","Namespace"),("resource","Recurso"),("container","Container"),("check","Check"),("evidence","Evidência"),("recommendation","Recomendação")]
        return self.layout("Manifest Quality", f'<h1>Manifest Quality</h1>{message}<div class="cis-actions"><a class="button" href="/manifests?collection={esc(directory.name)}">Exportar manifests sanitizados</a></div>{table(value.get("findings") or [],columns)}', directory, "manifests")

    def best_practices(self, directory: Path | None, query: dict[str, list[str]]) -> str:
        if not directory: return self.overview(None)
        value = (details(directory).get("operationalInsights") or {}).get("bestPractices") or {}
        rows = value.get("rules") or []
        provider, domain = query.get("provider", [""])[0], query.get("domain", [""])[0]
        if provider: rows = [x for x in rows if x.get("provider") == provider]
        if domain: rows = [x for x in rows if x.get("domain") == domain]
        providers = sorted({str(x.get("provider")) for x in value.get("rules") or []}); domains = sorted({str(x.get("domain")) for x in value.get("rules") or []})
        filters = f'<form class="filters"><input type="hidden" name="collection" value="{esc(directory.name)}"><label>Provider<select name="provider"><option value="">Todos</option>{"".join(f"<option>{esc(x)}</option>" for x in providers)}</select></label><label>Domínio<select name="domain"><option value="">Todos</option>{"".join(f"<option>{esc(x)}</option>" for x in domains)}</select></label><button>Filtrar</button></form>'
        columns = [("status","Status"),("provider","Provider"),("domain","Domínio"),("ruleId","Rule ID"),("applicability","Aplicabilidade"),("responsibility","Responsabilidade"),("resource","Recurso"),("evidence","Evidência"),("recommendation","Recomendação")]
        return self.layout("Best Practices", f'<h1>Best Practices <small>{esc(value.get("platform","UNKNOWN"))}</small></h1><div class="message warn">{esc(value.get("notice",""))}</div>{filters}{table(rows,columns)}', directory, "best")

    def logs(self, directory: Path | None) -> str:
        if not directory: return self.overview(None)
        value = (details(directory).get("operationalInsights") or {}).get("logs") or {}
        entries = []
        for item in value.get("entries") or []:
            entries.append(f'<details><summary>{esc(item.get("state"))} — {esc(item.get("target"))}</summary><pre>{esc(item.get("content") or item.get("reason") or item.get("error") or "Sem conteúdo")}</pre></details>')
        warning = '<div class="message warn">Logs são opcionais, podem conter dados sensíveis e somente são coletados com opt-in, targets explícitos, limite de tamanho e redaction. Nunca são usados para marcar PASS.</div>'
        content = "".join(entries) or '<div class="message">' + esc(value.get("reason", "Nenhum log coletado.")) + "</div>"
        body = f'<h1>Logs sanitizados</h1>{warning}<p>Estado: <b>{esc(value.get("state","DISABLED"))}</b> · bytes: {esc(value.get("bytes",0))}/{esc(value.get("maxBytes",0))}</p>{content}'
        return self.layout("Logs", body, directory, "logs")

    def capacity(self, directory: Path | None) -> str:
        if not directory: return self.overview(None)
        rows = []
        for item in details(directory)["capacity"]:
            rows.append({"namespace": item.get("namespace"), "workload": item.get("workload"), "window": item.get("window"), "replicas": item.get("replicasObserved"), "confidence": item.get("confidence"), "currentCpuRequest": (item.get("current") or {}).get("cpuRequestPerReplica"), "currentCpuLimit": (item.get("current") or {}).get("cpuLimitPerReplica"), "cpuRequest": (item.get("cpu") or {}).get("requestPerReplica"), "cpuLimit": (item.get("cpu") or {}).get("limitPerReplica"), "currentMemoryRequest": (item.get("current") or {}).get("memoryRequestPerReplica"), "currentMemoryLimit": (item.get("current") or {}).get("memoryLimitPerReplica"), "memoryRequest": (item.get("memory") or {}).get("requestPerReplica"), "memoryLimit": (item.get("memory") or {}).get("limitPerReplica"), "assessment": "; ".join(item.get("assessment") or []), "scaling": item.get("scalingRecommendation"), "caveat": item.get("caveat")})
        message = '<div class="message">Nenhuma proposta foi produzida. Configure uma URL Prometheus explícita e confirme séries de CPU e memória. Ausência de métricas não significa conformidade.</div>' if not rows else '<div class="message good">Propostas estatísticas: valide startup, sazonalidade, atribuição por container e throttling antes de alterar manifests.</div>'
        columns = [("namespace", "Namespace"), ("workload", "Workload"), ("window", "Janela"), ("replicas", "Réplicas"), ("confidence", "Confiança"), ("currentCpuRequest", "CPU req atual"), ("cpuRequest", "CPU req proposta"), ("currentCpuLimit", "CPU lim atual"), ("cpuLimit", "CPU lim proposta"), ("currentMemoryRequest", "Mem req atual"), ("memoryRequest", "Mem req proposta"), ("currentMemoryLimit", "Mem lim atual"), ("memoryLimit", "Mem lim proposta"), ("assessment", "Diagnóstico"), ("scaling", "HPA/KEDA"), ("caveat", "Ressalva")]
        return self.layout("Container Tuning", f'<h1>Container Tuning orientado por telemetria</h1>{message}{table(rows,columns)}', directory, "capacity")

    def prometheus(self, directory: Path | None) -> str:
        if not directory:
            return self.overview(None)
        value = details(directory)
        telemetry = value["telemetry"] if isinstance(value["telemetry"], dict) else {"state": "UNAVAILABLE", "workloads": []}
        workloads = telemetry.get("workloads") or []

        capacity = {}
        for item in value["capacity"]:
            reference = str(item.get("workload", ""))
            name = reference.split("/", 1)[-1]
            capacity[(str(item.get("namespace", "")), name)] = item

        manifest_runtime: dict[tuple[str, str], dict] = {}
        comprehensive = value.get("comprehensive") or {}
        for item in comprehensive.get("workloads") or []:
            if str(item.get("kind", "")) != "Deployment":
                continue
            runtime_names: set[str] = set()
            runtime_config: list[dict] = []
            for container in item.get("containers") or []:
                technologies = set(container.get("technologies") or [])
                if "Java" in technologies:
                    runtime_names.add("JVM")
                if ".NET" in technologies:
                    runtime_names.add(".NET")
                for name, raw_value in (container.get("runtimeOptions") or {}).items():
                    runtime_config.append({
                        "container": container.get("name", ""),
                        "name": name,
                        "value": raw_value,
                    })
            manifest_runtime[(str(item.get("namespace", "")), str(item.get("name", "")))] = {
                "runtimes": sorted(runtime_names),
                "config": runtime_config,
            }

        rows = []
        runtime_rows = []
        simple_rows = []
        technical_rows = []
        counters = Counter()
        runtime_coverage = 0
        technology_coverage = 0
        for workload in workloads:
            namespace = str(workload.get("namespace", ""))
            deployment = str(workload.get("deployment", ""))
            metrics = workload.get("metrics") or {}
            fallback_runtime = manifest_runtime.get((namespace, deployment), {"runtimes": [], "config": []})
            runtime_telemetry = workload.get("runtimeTelemetry") or []
            runtime_names = set(workload.get("runtimeDetected") or workload.get("runtimeHints") or [])
            runtime_names.update(fallback_runtime.get("runtimes") or [])
            runtime_names.update(
                str(entry.get("runtime"))
                for entry in runtime_telemetry
                if isinstance(entry, dict) and entry.get("runtime")
            )
            runtime_label = ", ".join(sorted(runtime_names)) or "N/A"
            technology_names = set(workload.get("technologiesDetected") or workload.get("technologyHints") or [])
            technology_label = ", ".join(sorted(technology_names)) or "N/A"
            if runtime_names:
                runtime_coverage += 1
            if technology_names:
                technology_coverage += 1

            def p95(*names: str) -> float | None:
                for name in names:
                    result = metric_p95(metrics, name)
                    if result is not None:
                        return result
                return None

            sizing = capacity.get((namespace, deployment), {})
            current = sizing.get("current") or {}
            replicas = max(1.0, finite_number(sizing.get("replicasObserved")) or 1.0)

            cpu = p95("cpu")
            memory = p95("memory_working_set", "memory")
            throttling = p95("cpu_throttling")
            heap_used = p95("jvm_heap_used", "dotnet_heap_used", "heap_used")
            heap_max = p95("jvm_heap_max", "dotnet_heap_max", "heap_max")
            gc = p95("jvm_gc", "dotnet_gc", "gc")
            threads = p95("jvm_threads", "dotnet_threads", "threads")
            allocation = p95("jvm_allocation", "dotnet_allocation")
            runtime_working_set = p95("dotnet_working_set")
            exceptions = p95("dotnet_exceptions")

            cpu_request = parse_cpu_quantity(current.get("cpuRequestPerReplica"))
            cpu_limit = parse_cpu_quantity(current.get("cpuLimitPerReplica"))
            memory_request = parse_memory_quantity(current.get("memoryRequestPerReplica"))
            memory_limit = parse_memory_quantity(current.get("memoryLimitPerReplica"))
            cpu_request_pct = ratio_percent(cpu, cpu_request * replicas if cpu_request is not None else None)
            cpu_limit_pct = ratio_percent(cpu, cpu_limit * replicas if cpu_limit is not None else None)
            memory_request_pct = ratio_percent(memory, memory_request * replicas if memory_request is not None else None)
            memory_limit_pct = ratio_percent(memory, memory_limit * replicas if memory_limit is not None else None)
            heap_pct = ratio_percent(heap_used, heap_max)

            critical = []
            warnings = []
            missing = []
            if cpu_limit_pct is not None:
                if cpu_limit_pct >= 95:
                    critical.append("CPU perto do limit")
                elif cpu_limit_pct >= 80:
                    warnings.append("CPU perto do limit")
            if memory_limit_pct is not None:
                if memory_limit_pct >= 90:
                    critical.append("memória perto do limit")
                elif memory_limit_pct >= 75:
                    warnings.append("memória perto do limit")
            if heap_pct is not None:
                if heap_pct >= 85:
                    critical.append("heap do runtime elevado")
                elif heap_pct >= 75:
                    warnings.append("heap do runtime elevado")
            if throttling is not None:
                if throttling >= 25:
                    critical.append("CPU throttling elevado")
                elif throttling >= 10:
                    warnings.append("CPU throttling elevado")
            if gc is not None:
                if gc >= 10:
                    critical.append("tempo de GC elevado")
                elif gc >= 5:
                    warnings.append("tempo de GC elevado")
            if cpu_request_pct is not None and cpu_request_pct >= 80:
                warnings.append("CPU acima de 80% do request")
            if memory_request_pct is not None and memory_request_pct >= 80:
                warnings.append("memória acima de 80% do request")
            if threads is not None and threads >= 300:
                warnings.append("threads do runtime acima de 300")
            if cpu_request is None:
                missing.append("request CPU ausente")
            if memory_request is None:
                missing.append("request memória ausente")
            if cpu_limit is None:
                missing.append("limit CPU ausente")
            if memory_limit is None:
                missing.append("limit memória ausente")
            if runtime_names and all(value is None for value in (heap_used, gc, threads, allocation)):
                missing.append("telemetria do runtime ausente")

            has_core_metrics = cpu is not None or memory is not None
            if not has_core_metrics:
                signal, css = "SEM DADOS", "na"
                diagnosis = workload.get("reason") or "Séries de CPU e memória não encontradas"
                counters["no_data"] += 1
            elif critical:
                signal, css = "CRÍTICO", "crit"
                diagnosis = "; ".join(critical + warnings)
                counters["critical"] += 1
            elif warnings:
                signal, css = "ATENÇÃO", "warn"
                diagnosis = "; ".join(warnings)
                counters["warning"] += 1
            elif missing:
                signal, css = "REVISAR", "info"
                diagnosis = "; ".join(missing)
                counters["review"] += 1
            else:
                signal, css = "OK", "ok"
                diagnosis = "Uso p95 dentro das referências configuradas"
                counters["ok"] += 1
            if len(diagnosis) > 180:
                diagnosis = diagnosis[:177] + "..."

            params = urlencode({"collection": directory.name, "namespace": namespace, "kind": "Deployment", "name": deployment})
            deployment_html = f'<a class="resource-link" href="/workload?{params}">{esc(deployment)}</a>'
            rows.append({
                "signalHtml": f'<span class="metric-status {css}">{esc(signal)}</span>',
                "namespace": namespace,
                "deploymentHtml": deployment_html,
                "runtime": runtime_label,
                "technologies": technology_label,
                "cpu": human_cpu(cpu),
                "cpuRequestHtml": percent_html(cpu_request_pct),
                "cpuLimitHtml": percent_html(cpu_limit_pct, 80, 95),
                "memory": human_bytes(memory),
                "memoryRequestHtml": percent_html(memory_request_pct),
                "memoryLimitHtml": percent_html(memory_limit_pct, 75, 90),
                "heapHtml": percent_html(heap_pct, 75, 85),
                "throttlingHtml": percent_html(throttling, 10, 25),
                "diagnosis": diagnosis,
            })

            config_entries = workload.get("runtimeConfig") or fallback_runtime.get("config") or []
            config_lines = []
            seen_config = set()
            for entry in config_entries:
                if not isinstance(entry, dict):
                    continue
                config_key = (
                    str(entry.get("container", "")),
                    str(entry.get("name", "")),
                    str(entry.get("value", "")),
                )
                if not config_key[1] or config_key in seen_config:
                    continue
                seen_config.add(config_key)
                config_lines.append(
                    f'<div><small>{esc(config_key[0])}</small> '
                    f'<code>{esc(config_key[1])}</code> = {esc(config_key[2])}</div>'
                )
            config_html = (
                f'<details class="runtime-config"><summary>{len(config_lines)} opção(ões) aplicada(s)</summary>'
                f'{"".join(config_lines)}</details>'
                if config_lines else "N/A"
            )

            metadata_parts = []
            detected_by = []
            for entry in runtime_telemetry:
                if not isinstance(entry, dict):
                    continue
                if entry.get("detectedBy"):
                    detected_by.append(str(entry.get("detectedBy")))
                for key, raw_value in sorted((entry.get("metadata") or {}).items()):
                    if raw_value not in (None, ""):
                        metadata_parts.append(f"{key}={raw_value}")
            metadata_text = "; ".join(dict.fromkeys(metadata_parts)) or "N/A"
            bindings = workload.get("metricBindings") or {}
            source_metrics = sorted({
                str(binding.get("sourceMetric"))
                for binding in bindings.values()
                if isinstance(binding, dict) and binding.get("sourceMetric")
            })
            source_text = ", ".join(source_metrics) or "N/A"
            discovery_text = ", ".join(dict.fromkeys(detected_by)) or (
                "manifest" if runtime_names else "N/A"
            )
            if runtime_names or config_lines or runtime_telemetry:
                runtime_rows.append({
                    "namespace": namespace,
                    "deploymentHtml": deployment_html,
                    "runtime": runtime_label,
                    "metadata": metadata_text,
                    "heap": (
                        f"{human_bytes(heap_used)} / {human_bytes(heap_max)}"
                        if heap_used is not None or heap_max is not None else "N/A"
                    ),
                    "heapHtml": percent_html(heap_pct, 75, 85),
                    "gcHtml": percent_html(gc, 5, 10),
                    "threads": "N/A" if threads is None else f"{threads:.0f}",
                    "allocation": "N/A" if allocation is None else f"{human_bytes(allocation)}/s",
                    "workingSet": human_bytes(runtime_working_set),
                    "exceptions": "N/A" if exceptions is None else f"{exceptions:.4g}/s",
                    "discovery": discovery_text,
                    "sources": source_text,
                    "configHtml": config_html,
                })

            for simple in workload.get("simpleMetrics") or []:
                if not isinstance(simple, dict):
                    continue
                simple_rows.append({
                    "namespace": namespace,
                    "deploymentHtml": deployment_html,
                    "technologies": technology_label,
                    **simple,
                })

            for metric, metric_value in sorted(metrics.items()):
                technical_rows.append({
                    "namespace": namespace,
                    "deployment": deployment,
                    "metric": metric,
                    "source": metric_value.get("source_metric", ""),
                    "runtime": metric_value.get("runtime", ""),
                    "unit": metric_value.get("unit", ""),
                    "state": metric_value.get("state"),
                    "mean": metric_value.get("mean"),
                    "peak": metric_value.get("peak"),
                    "p50": metric_value.get("p50"),
                    "p90": metric_value.get("p90"),
                    "p95": metric_value.get("p95"),
                    "p99": metric_value.get("p99"),
                    "samples": metric_value.get("samples"),
                    "reason": metric_value.get("reason", ""),
                })

        state = str(telemetry.get("state", "DISABLED"))
        state_labels = {
            "AVAILABLE": "Disponível",
            "PARTIAL": "Parcial",
            "UNAVAILABLE": "Indisponível",
            "DISABLED": "Desabilitado",
        }
        available = sum(1 for item in workloads if item.get("state") == "AVAILABLE")
        message_class = "good" if state == "AVAILABLE" else "bad" if state == "UNAVAILABLE" else ""
        reason = str(telemetry.get("reason") or "").strip()
        discovery = telemetry.get("metricDiscovery") or {}
        platform = telemetry.get("platformHealth") or {}
        targets = platform.get("targets") or {}
        rules = platform.get("rules") or {}
        catalog_metrics = int(discovery.get("catalogMetrics") or 0)
        catalog_source = str(discovery.get("catalogSource") or "N/A")
        message = (
            f'<div class="message {message_class}">Coletor Python: '
            f'<b>{esc(state_labels.get(state, state))}</b>. Janela: '
            f'<b>{esc(telemetry.get("window", "-"))}</b>. {esc(reason)} '
            f'Catálogo automático: <b>{catalog_metrics}</b> métricas via {esc(catalog_source)}. '
            'Ausência de série é N/A, nunca conformidade.</div>'
        )
        facts = (
            '<div class="facts prometheus-facts">'
            f'<div><small>Estado</small><b>{esc(state_labels.get(state, state))}</b></div>'
            f'<div><small>Cobertura básica</small><b>{available}/{len(workloads)}</b><span> deployments</span></div>'
            f'<div><small>Runtimes descobertos</small><b>{runtime_coverage}</b></div>'
            f'<div><small>Tecnologias com métricas</small><b>{technology_coverage}</b></div>'
            f'<div><small>Targets Prometheus UP</small><b>{targets.get("up", "N/A")}/{targets.get("active", "N/A")}</b></div>'
            f'<div><small>Rules não saudáveis</small><b>{rules.get("unhealthy", "N/A")}</b></div>'
            f'<div><small>Alertas firing</small><b>{rules.get("firing", "N/A")}</b></div>'
            f'<div><small>Críticos</small><b>{counters["critical"]}</b></div>'
            f'<div><small>Atenções</small><b>{counters["warning"]}</b></div>'
            f'<div><small>Revisar configuração</small><b>{counters["review"]}</b></div>'
            f'<div><small>Sem séries básicas</small><b>{counters["no_data"]}</b></div>'
            '</div>'
        )
        legend = (
            '<div class="metric-legend"><b>Como ler:</b> p95 é o uso que não foi ultrapassado '
            'em 95% das amostras. “% request/limit” compara o uso com as réplicas observadas. '
            'JVM/.NET, Kafka, RabbitMQ, NGINX e gateways são descobertos no Prometheus e nos manifests; '
            'as opções aplicadas vêm dos manifests sanitizados. N/A significa dado ou configuração ausente.</div>'
        )
        usage_columns = [
            ("signalHtml", "Sinal"), ("namespace", "Namespace"),
            ("deploymentHtml", "Deployment"), ("runtime", "Runtime"),
            ("technologies", "Tecnologias"), ("cpu", "CPU p95"), ("cpuRequestHtml", "CPU / request"),
            ("cpuLimitHtml", "CPU / limit"), ("memory", "Memória p95"),
            ("memoryRequestHtml", "Memória / request"),
            ("memoryLimitHtml", "Memória / limit"),
            ("heapHtml", "Heap runtime"), ("throttlingHtml", "Throttling"),
            ("diagnosis", "Leitura rápida"),
        ]
        usage_table = table(
            rows,
            usage_columns,
            {
                "signalHtml", "deploymentHtml", "cpuRequestHtml", "cpuLimitHtml",
                "memoryRequestHtml", "memoryLimitHtml", "heapHtml", "throttlingHtml",
            },
        )
        runtime_columns = [
            ("namespace", "Namespace"), ("deploymentHtml", "Deployment"),
            ("runtime", "Runtime"), ("metadata", "Versão / implementação"),
            ("heap", "Heap p95 / máximo"), ("heapHtml", "Heap %"),
            ("gcHtml", "GC p95"), ("threads", "Threads p95"),
            ("allocation", "Alocação p95"), ("workingSet", "Working set p95"),
            ("exceptions", "Exceções p95"), ("discovery", "Detectado por"),
            ("sources", "Métricas-fonte"), ("configHtml", "Opções aplicadas"),
        ]
        runtime_table = (
            table(runtime_rows, runtime_columns, {"deploymentHtml", "heapHtml", "gcHtml", "configHtml"})
            if runtime_rows
            else '<div class="message">Nenhum runtime foi identificado por manifest ou série Prometheus nesta coleta.</div>'
        )
        platform_rows = [
            {"domain": "Targets", "state": platform.get("state", "N/A"), "total": targets.get("active", "N/A"), "healthy": targets.get("up", "N/A"), "problem": targets.get("down", "N/A"), "detail": f'erros de scrape={targets.get("scrapeErrors", "N/A")}'},
            {"domain": "Rules", "state": platform.get("state", "N/A"), "total": rules.get("rules", "N/A"), "healthy": "N/A", "problem": rules.get("unhealthy", "N/A"), "detail": f'alertas firing={rules.get("firing", "N/A")}; pending={rules.get("pending", "N/A")}'},
        ]
        simple_table = table(
            simple_rows,
            [("namespace","Namespace"),("deploymentHtml","Deployment"),("technologies","Tecnologias"),("metric","Sinal"),("state","Estado"),("mean","Média"),("p95","p95"),("peak","Pico"),("assessment","Leitura rápida")],
            {"deploymentHtml"},
        )
        technical_table = table(
            technical_rows,
            [
                ("namespace", "Namespace"), ("deployment", "Deployment"),
                ("metric", "Métrica normalizada"), ("source", "Métrica-fonte"),
                ("runtime", "Runtime"), ("unit", "Unidade"), ("state", "Estado"),
                ("mean", "Média"), ("peak", "Pico"), ("p50", "p50"),
                ("p90", "p90"), ("p95", "p95"), ("p99", "p99"),
                ("samples", "Amostras"), ("reason", "Detalhe"),
            ],
        )
        baseline = table(
            value["metrics"],
            [("metric", "Métrica baseline"), ("value", "Valor"), ("promql", "PromQL")],
        )
        technical = (
            f'<details class="technical"><summary>Exibir métricas técnicas e percentis completos</summary>'
            f'{technical_table}</details>'
            f'<details class="technical"><summary>Exibir baseline pontual e PromQL</summary>'
            f'{baseline}</details>'
        )
        body = (
            f'<h1>Prometheus — visão operacional</h1>{message}{facts}{legend}'
            f'<h2>Saúde do Prometheus</h2>{table(platform_rows, [("domain","Domínio"),("state","Estado"),("total","Total"),("healthy","Saudáveis"),("problem","Problemas"),("detail","Detalhe")])}'
            f'<h2>Uso p95 por Deployment</h2>{usage_table}'
            f'<h2>Sinais simplificados e tecnologias</h2>{simple_table}'
            f'<h2>Runtime e tuning descobertos automaticamente</h2>{runtime_table}'
            f'{technical}'
        )
        return self.layout("Prometheus", body, directory, "prometheus")

    def cloud_provider(self, directory: Path | None) -> str:
        if not directory:
            return self.overview(None)
        cloud = details(directory).get("cloudProvider") or {"state": "N/A", "provider": "generic-kubernetes"}
        provider = str(cloud.get("provider") or "generic-kubernetes")
        state = str(cloud.get("state") or "UNKNOWN")
        lifecycle = cloud.get("lifecycle") or {}
        summary = cloud.get("summary") or {}
        safety = cloud.get("safety") or {}
        coverage_rows = [{"domain": key, "state": item.get("state"), "reason": item.get("reason", "")} for key, item in sorted((cloud.get("coverage") or {}).items()) if isinstance(item, dict)]
        cluster_rows = [{"field": key, "value": json.dumps(item, ensure_ascii=False) if isinstance(item, (dict, list)) else item} for key, item in sorted((cloud.get("cluster") or {}).items())]
        lifecycle_rows = [{"field": key, "value": json.dumps(item, ensure_ascii=False) if isinstance(item, (dict, list)) else item} for key, item in sorted(lifecycle.items())]
        practices = cloud.get("bestPractices") or []
        message_class = "good" if state == "AVAILABLE" else "bad" if state == "UNAVAILABLE" else ""
        facts = (
            '<div class="facts">'
            f'<div><small>Provider</small><b>{esc(provider)}</b></div>'
            f'<div><small>Estado</small><b>{esc(state)}</b></div>'
            f'<div><small>Chamadas read-only</small><b>{esc(safety.get("requests",0))}</b></div>'
            f'<div><small>Regras</small><b>{esc(summary.get("rules",0))}</b></div>'
            f'<div><small>Lifecycle</small><b>{esc(lifecycle.get("supportState","UNKNOWN"))}</b></div>'
            '</div>'
        )
        body = (
            f'<h1>Cloud Provider <small>{esc(provider)}</small></h1><div class="message {message_class}">'
            f'Evidência normalizada e estritamente read-only: <b>{esc(state)}</b>. {esc(cloud.get("reason",""))} '
            'Payloads brutos, credenciais e identificadores de conta não são persistidos.</div>{facts}'
            f'<div class="cis-actions"><a class="button" href="/export-cloud?collection={esc(directory.name)}">Exportar evidência sanitizada</a></div>'
            f'<h2>Lifecycle</h2>{table(lifecycle_rows, [("field","Campo"),("value","Valor")])}'
            f'<h2>Configuração sanitizada</h2>{table(cluster_rows, [("field","Campo"),("value","Valor")])}'
            f'<h2>Best Practices comprováveis</h2>{table(practices, [("status","Status"),("domain","Domínio"),("ruleId","Rule ID"),("evidence","Evidência"),("recommendation","Recomendação")])}'
            f'<h2>Cobertura das Cloud Provider APIs</h2>{table(coverage_rows, [("domain","Domínio"),("state","Estado"),("reason","Detalhe")])}'
        )
        return self.layout("Cloud Provider", body, directory, "cloud")

    def aws_eks(self, directory: Path | None) -> str:
        if not directory:
            return self.overview(None)
        value = details(directory)
        aws = value.get("awsEks") or {"state": "UNKNOWN"}
        state = str(aws.get("state") or "UNKNOWN")
        reason = str(aws.get("reason") or "")
        summary = aws.get("summary") or {}
        coverage_rows = [
            {
                "domain": key,
                "state": entry.get("state"),
                "reason": entry.get("reason", ""),
            }
            for key, entry in sorted((aws.get("coverage") or {}).items())
            if isinstance(entry, dict)
        ]
        inventory_rows = []
        for key, entry in sorted((aws.get("inventory") or {}).items()):
            if isinstance(entry, list):
                count, detail = len(entry), f"{len(entry)} objeto(s)"
            elif isinstance(entry, dict):
                count = len(entry)
                detail = json.dumps(entry, ensure_ascii=False, sort_keys=True)
            else:
                count, detail = 1, str(entry)
            if len(detail) > 600:
                detail = detail[:597] + "..."
            inventory_rows.append({"domain": key, "count": count, "detail": detail})
        aws_findings = [
            finding
            for finding in value["findings"]
            if finding.get("evidence") == "aws-api" or finding.get("category") in {"EKS", "AWS Security", "EKS Networking"}
        ]
        message_class = "good" if state == "AVAILABLE" else "bad" if state == "UNAVAILABLE" else ""
        facts = (
            '<div class="facts">'
            f'<div><small>Estado</small><b>{esc(state)}</b></div>'
            f'<div><small>Checks AWS</small><b>{summary.get("checks", 0)}</b></div>'
            f'<div><small>Críticos</small><b>{summary.get("critical", 0)}</b></div>'
            f'<div><small>Alertas</small><b>{summary.get("warnings", 0)}</b></div>'
            f'<div><small>Desconhecidos</small><b>{summary.get("unknown", 0)}</b></div>'
            f'<div><small>Cobertura disponível</small><b>{summary.get("coverageAvailable", 0)}</b></div>'
            '</div>'
        )
        body = (
            f'<h1>AWS / Amazon EKS</h1><div class="message {message_class}">'
            f'Coletor exclusivamente read-only: <b>{esc(state)}</b>. {esc(reason)} '
            'Permissão ausente vira UNKNOWN; não é tratada como conformidade.</div>'
            f'{facts}<h2>Achados e recomendações</h2>'
            f'{table(aws_findings, [("severity","Severidade"),("check","Check"),("workload","Recurso"),("detail","Evidência"),("recommendation","Recomendação")])}'
            f'<h2>Cobertura das APIs AWS</h2>{table(coverage_rows, [("domain","Domínio"),("state","Estado"),("reason","Detalhe")])}'
            f'<h2>Inventário sanitizado</h2>{table(inventory_rows, [("domain","Domínio"),("count","Itens"),("detail","Resumo")])}'
        )
        return self.layout("AWS / EKS", body, directory, "aws")

    def cis_security(self, directory: Path | None, query: dict[str, list[str]]) -> str:
        if not directory:
            return self.overview(None)
        report = details(directory).get("cisSecurity") or {}
        if not report:
            return self.layout("CIS Security", '<h1>CIS Security</h1><div class="message">Esta coleta é anterior à geração do artefato CIS Security. Execute uma nova coleta.</div>', directory, "cis")
        summary = report.get("summary") or {}
        controls = report.get("controls") or []
        filters = {key: query.get(key, [""])[0] for key in ("status", "applicability", "responsibility", "source")}
        search = query.get("search", [""])[0].lower().strip()
        rows = [item for item in controls if all(not value or str(item.get({"responsibility": "managedResponsibility", "source": "evidenceSource"}.get(key, key), "")) == value for key, value in filters.items()) and (not search or search in json.dumps(item, ensure_ascii=False).lower())]
        def select(name: str, label: str, values: list[str]) -> str:
            options = ''.join(f'<option value="{esc(value)}" {"selected" if filters[name] == value else ""}>{esc(value)}</option>' for value in values)
            return f'<label>{label}<select name="{name}"><option value="">Todos</option>{options}</select></label>'
        filter_form = (
            f'<form class="filters"><input type="hidden" name="collection" value="{esc(directory.name)}">'
            f'{select("status", "Status", sorted({str(x.get("status")) for x in controls}))}'
            f'{select("applicability", "Aplicabilidade", sorted({str(x.get("applicability")) for x in controls}))}'
            f'{select("responsibility", "Responsabilidade", sorted({str(x.get("managedResponsibility")) for x in controls}))}'
            f'{select("source", "Evidence Source", sorted({str(x.get("evidenceSource")) for x in controls}))}'
            f'<input name="search" value="{esc(query.get("search", [""])[0])}" placeholder="Control ID, controle, evidência ou recomendação"><button>Filtrar</button>'
            f'<a class="button" href="/cis-security?collection={quote_plus(directory.name)}">Limpar</a></form>'
        )
        control_cards = []
        for item in rows:
            evidence = json.dumps(item.get("evidence") or {}, ensure_ascii=False, indent=2, sort_keys=True)
            control_cards.append(
                f'<details class="cis-control {esc(str(item.get("status", "UNKNOWN")))}"><summary><span><b>{esc(item.get("controlId"))}</b> — {esc(item.get("title"))}</span><span class="metric-status {esc(str(item.get("status", "UNKNOWN")).lower())}">{esc(item.get("status"))}</span></summary>'
                f'<div class="cis-meta"><span>Domínio: <b>{esc(item.get("domain"))}</b></span><span>Prioridade: <b>{esc(item.get("priority"))}</b></span><span>Esforço: <b>{esc(item.get("effort"))}</b></span><span>Impacto: <b>{esc(item.get("impact"))}</b></span><span>Aplicabilidade: <b>{esc(item.get("applicability"))}</b></span><span>Responsabilidade: <b>{esc(item.get("managedResponsibility"))}</b></span><span>Fonte: <b>{esc(item.get("evidenceSource"))}</b></span><span>Modo: <b>{esc(item.get("assessmentMode"))}</b></span></div>'
                f'<h3>Evidência sanitizada</h3><pre>{esc(evidence)}</pre><h3>Lifecycle</h3><pre>{esc(json.dumps(item.get("remediation") or {}, ensure_ascii=False, indent=2))}</pre><h3>Recomendação</h3><p>{esc(item.get("recommendation"))}</p><h3>Validação read-only</h3><pre>{esc(item.get("validationCommand"))}</pre><h3>Exemplo de remediação</h3><p>{esc(item.get("remediationExample"))}</p></details>'
            )
        score = summary.get("postureScorePercent", summary.get("scorePercent"))
        controls_html = "".join(control_cards) if control_cards else '<div class="message">Nenhum controle corresponde aos filtros.</div>'
        facts = (
            '<div class="facts">'
            f'<div><small>Plataforma</small><b>{esc(report.get("platform", "UNKNOWN"))}</b></div>'
            f'<div><small>Controles</small><b>{summary.get("controls", 0)}</b></div>'
            f'<div><small>Avaliados no score</small><b>{summary.get("scored", 0)}</b></div>'
            f'<div><small>PASS</small><b>{summary.get("passed", 0)}</b></div>'
            f'<div><small>WARN</small><b>{summary.get("warnings", 0)}</b></div>'
            f'<div><small>Managed Provider</small><b>{(summary.get("applicability") or {}).get("MANAGED_PROVIDER", 0)}</b></div>'
            f'<div><small>Manual Review</small><b>{(summary.get("applicability") or {}).get("MANUAL_REVIEW", 0)}</b></div>'
            f'<div><small>Evidence Unavailable</small><b>{(summary.get("applicability") or {}).get("EVIDENCE_UNAVAILABLE", 0)}</b></div>'
            f'<div><small>Posture Score</small><b>{esc(str(score) + "%" if score is not None else "N/A")}</b></div>'
            f'<div><small>Evidence Coverage</small><b>{esc(str(summary.get("evidenceCoveragePercent")) + "%" if summary.get("evidenceCoveragePercent") is not None else "N/A")}</b></div>'
            '</div>'
        )
        domain_rows = summary.get("domains") or []
        recommendations = sorted((item for item in controls if item.get("status") == "WARN"), key=lambda item: (-int(item.get("riskWeight") or 0), {"LOW": 0, "MEDIUM": 1, "HIGH": 2}.get(str(item.get("effort")), 9), str(item.get("controlId"))))
        recommendation_rows = [{"priority": item.get("priority"), "domain": item.get("domain"), "controlId": item.get("controlId"), "title": item.get("title"), "impact": item.get("impact"), "effort": item.get("effort"), "recommendation": item.get("recommendation"), "validationCommand": item.get("validationCommand")} for item in recommendations]
        comparison = ""
        cis_directories = [item for item in self.directories() if (item / "cis-security-assessment.json").is_file()]
        before_id = query.get("before", [""])[0]
        before_options = ''.join(f'<option value="{esc(item.name)}" {"selected" if item.name == before_id else ""}>{esc(item.name)}</option>' for item in cis_directories if item != directory)
        comparison_form = f'<form class="compare"><input type="hidden" name="collection" value="{esc(directory.name)}"><label>Coleta anterior<select name="before"><option value="">Selecionar</option>{before_options}</select></label><button>Comparar CIS</button></form>'
        before_dir = self.root / before_id
        if before_id and before_dir.is_dir() and (before_dir / "cis-security-assessment.json").is_file():
            delta = compare_reports(jfile(before_dir / "cis-security-assessment.json", {}), report)
            comparison = (
                f'<div class="facts"><div><small>Delta Posture</small><b>{delta.get("postureDelta", 0):+}%</b></div><div><small>Delta Coverage</small><b>{delta.get("coverageDelta", 0):+}%</b></div><div><small>Regressões</small><b>{(delta.get("counts") or {}).get("REGRESSION", 0)}</b></div><div><small>Resolvidos</small><b>{(delta.get("counts") or {}).get("RESOLVED", 0)}</b></div><div><small>Evidence Loss</small><b>{(delta.get("counts") or {}).get("EVIDENCE_LOSS", 0)}</b></div></div>'
                + table(delta.get("changes") or [], [("change","Mudança"),("controlId","Control ID"),("domain","Domínio"),("beforeStatus","Status anterior"),("afterStatus","Status atual"),("beforeApplicability","Aplicabilidade anterior"),("afterApplicability","Aplicabilidade atual")])
            )
        body = (
            f'<h1>CIS Security</h1><div class="message warn"><b>{esc(report.get("notice"))}</b> '
            'Controles gerenciados pelo provider, revisões manuais e evidência indisponível não reduzem artificialmente o score.</div>'
            f'{facts}<div class="cis-actions"><a class="button" href="/export-cis?collection={quote_plus(directory.name)}">Exportar relatório CIS JSON</a><a class="button" href="/cis-report?collection={quote_plus(directory.name)}">Relatório executivo / PDF</a></div>'
            f'<h2>Score por domínio</h2>{table(domain_rows, [("domain","Domínio"),("controls","Controles avaliados"),("passed","PASS"),("scorePercent","Posture Score %")])}'
            f'<h2>Plano de ação priorizado</h2>{table(recommendation_rows, [("priority","Prioridade"),("domain","Domínio"),("controlId","Control ID"),("title","Controle"),("impact","Impacto"),("effort","Esforço"),("recommendation","Recomendação"),("validationCommand","Validação read-only")])}'
            f'<h2>Comparação CIS</h2>{comparison_form}{comparison}'
            f'<h2>Controles por evidência e responsabilidade <small>{len(rows)} resultado(s)</small></h2>{filter_form}{controls_html}'
        )
        return self.layout("CIS Security", body, directory, "cis")

    def cis_report(self, directory: Path | None) -> str:
        if not directory: return self.overview(None)
        report = details(directory).get("cisSecurity") or {}; summary = report.get("summary") or {}
        if not report: return self.layout("Relatório CIS", '<div class="message">Relatório CIS indisponível.</div>', directory, "cis")
        actions = sorted((c for c in report.get("controls") or [] if c.get("status") == "WARN"), key=lambda c: (-int(c.get("riskWeight") or 0), str(c.get("controlId"))))
        rows = [{"priority": c.get("priority"), "domain": c.get("domain"), "control": c.get("title"), "owner": (c.get("remediation") or {}).get("owner", "Não atribuído"), "due": (c.get("remediation") or {}).get("dueDate", "-"), "state": (c.get("remediation") or {}).get("state", "OPEN"), "recommendation": c.get("recommendation")} for c in actions]
        body = f'<div class="print-only-note">Use Imprimir → Salvar como PDF.</div><h1>Relatório executivo — CIS Security</h1><p><b>Coleta:</b> {esc(directory.name)} · <b>Plataforma:</b> {esc(report.get("platform"))}</p><div class="message warn">{esc(report.get("notice"))}</div><div class="facts"><div><small>Posture Score</small><b>{summary.get("postureScorePercent","N/A")}%</b></div><div><small>Evidence Coverage</small><b>{summary.get("evidenceCoveragePercent","N/A")}%</b></div><div><small>Riscos</small><b>{summary.get("warnings",0)}</b></div><div><small>Evidências externas</small><b>{summary.get("acceptedExternalEvidence",0)}</b></div></div><h2>Score por domínio</h2>{table(summary.get("domains") or [], [("domain","Domínio"),("controls","Controles"),("passed","PASS"),("scorePercent","Score %")])}<h2>Plano de ação</h2>{table(rows, [("priority","Prioridade"),("domain","Domínio"),("control","Controle"),("owner","Owner"),("due","Prazo"),("state","Estado"),("recommendation","Recomendação")])}<h2>Matriz de responsabilidade</h2>{table([{"responsibility": k, "controls": v} for k,v in (summary.get("responsibility") or {}).items()], [("responsibility","Responsabilidade"),("controls","Controles")])}'
        return self.layout("Relatório executivo CIS", body, directory, "cis")

    def coverage(self, directory: Path | None) -> str:
        if not directory: return self.overview(None)
        value = details(directory)
        rows = [{"resource": key, "state": entry.get("state"), "count": entry.get("count", 0), "api": entry.get("resource", "-"), "reason": entry.get("reason", "")} for key, entry in sorted(value["coverage"].items())]
        discovery = value.get("discovery") or {}; universal = value.get("universal") or {}
        performance = (value.get("comprehensive") or {}).get("performance") or {}
        quality = (value.get("comprehensive") or {}).get("quality") or {}
        request_budget = performance.get("requestBudget") or {}
        metadata_performance = (value.get("metadata") or {}).get("performance") or {}
        duration = metadata_performance.get("durationSeconds")
        peak_rss = performance.get("processPeakRssBytes")
        performance_facts = (
            '<div class="facts">'
            f'<div><small>Duração total</small><b>{esc(str(duration) + "s" if duration is not None else "N/A")}</b></div>'
            f'<div><small>Kubernetes API requests</small><b>{esc(request_budget.get("requests","N/A"))}</b></div>'
            f'<div><small>Retries / throttles</small><b>{esc(request_budget.get("retries","N/A"))} / {esc(request_budget.get("throttles","N/A"))}</b></div>'
            f'<div><small>Response bytes</small><b>{esc(request_budget.get("responseBytes","N/A"))}</b></div>'
            f'<div><small>Peak RSS</small><b>{esc(human_bytes(float(peak_rss)) if peak_rss is not None else "N/A")}</b></div>'
            '</div>'
        )
        universal_rows = []
        for entry in universal.get("resources") or []:
            params = urlencode({"collection": directory.name, "resource": entry.get("resource", "")})
            universal_rows.append({"resourceHtml": f'<a class="resource-link" href="/api-inventory?{params}">{esc(entry.get("resource"))}</a>', "scope": entry.get("scope"), "state": entry.get("state"), "count": entry.get("count", 0), "mode": "profunda" if entry.get("deepCollected") else "identidade segura", "reason": entry.get("reason", "")})
        message = f'<div class="message good">Somente leitura. Secrets: metadados/chaves, sem valores. Discovery: {discovery.get("succeeded","N/A")} concluídas, {discovery.get("not_applicable","N/A")} N/A, {discovery.get("unavailable","N/A")} indisponíveis. Inventário universal: {universal.get("resourceTypes",0)} APIs e {universal.get("objectCount",0)} objetos; indisponíveis: {universal.get("unavailableResourceTypes",0)}.</div>'
        known = table(rows, [("resource", "Domínio profundo"), ("state", "Estado"), ("count", "Objetos"), ("api", "API usada"), ("reason", "Detalhe")])
        all_apis = table(universal_rows, [("resourceHtml", "API/recurso"), ("scope", "Escopo"), ("state", "Estado"), ("count", "Objetos"), ("mode", "Coleta"), ("reason", "Detalhe")], {"resourceHtml"})
        quality_facts = f'<div class="facts"><div><small>Quality gate</small><b>{esc(quality.get("state","UNKNOWN"))}</b></div><div><small>Identidades duplicadas</small><b>{esc(quality.get("stableIdentityDuplicates",0))}</b></div><div><small>Severidades conflitantes</small><b>{esc(quality.get("conflictingSeverities",0))}</b></div><div><small>PASS com baixa confiança</small><b>{esc(quality.get("lowConfidencePasses",0))}</b></div></div>'
        calibration = table(quality.get("falsePositiveReviewCandidates") or [], [("ruleId","Rule ID"),("findings","Findings"),("reason","Motivo da revisão")])
        return self.layout("Cobertura", f'<h1>Cobertura da descoberta</h1>{message}<h2>Impacto medido</h2>{performance_facts}<h2>Quality gate e calibração</h2>{quality_facts}{calibration}<h2>Domínios com análise profunda</h2>{known}<h2>Todas as APIs listáveis</h2>{all_apis}', directory, "coverage")

    def api_inventory(self, directory: Path | None, query: dict[str, list[str]]) -> str:
        if not directory: return self.overview(None)
        resource = query.get("resource", [""])[0]; universal = details(directory).get("universal") or {}
        entry = next((item for item in universal.get("resources") or [] if item.get("resource") == resource), None)
        if not entry: return self.layout("Inventário de API", '<div class="message bad">Recurso não encontrado no inventário universal.</div>', directory, "coverage")
        rows = entry.get("objects") or []
        mode = "coleta profunda" if entry.get("deepCollected") else "somente identidade segura"
        message = f'<div class="message">Estado: <b>{esc(entry.get("state"))}</b> · Escopo: {esc(entry.get("scope"))} · Modo: {mode} · Objetos: {len(rows)}. Recursos desconhecidos não persistem spec ou valores.</div>'
        return self.layout(resource, f'<h1>{esc(resource)}</h1>{message}{table(rows,[("apiVersion","API version"),("kind","Kind"),("namespace","Namespace"),("name","Nome")])}', directory, "coverage")
    def compare(self, directory: Path | None, query: dict[str, list[str]]) -> str:
        directories = self.directories()
        before_id = query.get("before", [""])[0]
        after_id = query.get("after", [directory.name if directory else ""])[0]
        def options(selected):
            return "".join(
                f'<option value="{esc(item.name)}" {"selected" if item.name == selected else ""}>{esc(item.name)}</option>'
                for item in directories
            )
        form = (
            f'<form class="compare"><label>Antes<select name="before">{options(before_id)}</select></label>'
            f'<label>Depois<select name="after">{options(after_id)}</select></label><button>Comparar</button></form>'
        )
        body = form
        before, after = self.root / before_id, self.root / after_id
        if before.is_dir() and after.is_dir():
            old, new = details(before), details(after)
            rows = [
                {
                    "metric": key,
                    "before": old["summary"].get(key),
                    "after": new["summary"].get(key),
                    "delta": (new["summary"].get(key, 0) or 0) - (old["summary"].get(key, 0) or 0),
                }
                for key in new["summary"]
                if isinstance(new["summary"].get(key), int)
                and isinstance(old["summary"].get(key), int)
            ]
            def fingerprint(item):
                return (
                    item.get("fingerprint")
                    or (
                        f'{item.get("ruleId")}|{item.get("resourceKey")}'
                        if item.get("ruleId") and item.get("resourceKey") else ""
                    )
                    or item.get("id")
                )
            old_map = {fingerprint(item): item for item in old["findings"] if fingerprint(item)}
            new_map = {fingerprint(item): item for item in new["findings"] if fingerprint(item)}
            risks = {"CRIT", "WARN"}
            gaps = {"UNKNOWN", "PARTIAL"}
            new_risks = [
                item for key, item in new_map.items()
                if item.get("severity") in risks and key not in old_map
            ]
            resolved = [
                item for key, item in old_map.items()
                if item.get("severity") in risks and key not in new_map
            ]
            new_gaps = [
                item for key, item in new_map.items()
                if item.get("severity") in gaps and key not in old_map
            ]
            severity_changes = []
            evidence_changes = []
            for key in sorted(old_map.keys() & new_map.keys()):
                previous, current = old_map[key], new_map[key]
                old_severity, new_severity = previous.get("severity"), current.get("severity")
                if old_severity != new_severity:
                    old_rank = SEVERITY_ORDER.get(old_severity, 9)
                    new_rank = SEVERITY_ORDER.get(new_severity, 9)
                    severity_changes.append({
                        "direction": "REGRESSÃO" if new_rank < old_rank else "MELHORIA",
                        "before": old_severity,
                        "after": new_severity,
                        "category": current.get("category"),
                        "workload": current.get("workload"),
                        "check": current.get("check"),
                        "detail": current.get("detail"),
                    })
                elif previous.get("evidenceHash") and previous.get("evidenceHash") != current.get("evidenceHash"):
                    evidence_changes.append({
                        "severity": new_severity,
                        "category": current.get("category"),
                        "workload": current.get("workload"),
                        "check": current.get("check"),
                        "before": previous.get("detail"),
                        "after": current.get("detail"),
                    })
            finding_columns = [("severity","Severidade"),("category","Categoria"),("namespace","Namespace"),("workload","Workload"),("check","Check"),("detail","Evidência")]
            body += '<h2>Delta dos indicadores</h2>' + table(rows, [("metric","Indicador"),("before","Antes"),("after","Depois"),("delta","Delta")])
            body += '<h2>Mudanças de severidade</h2>' + table(severity_changes, [("direction","Direção"),("before","Antes"),("after","Depois"),("category","Categoria"),("workload","Workload"),("check","Check"),("detail","Evidência")])
            body += '<h2>Novos riscos</h2>' + table(new_risks, finding_columns)
            body += '<h2>Riscos resolvidos</h2>' + table(resolved, finding_columns)
            body += '<h2>Novas lacunas de evidência</h2>' + table(new_gaps, finding_columns)
            body += '<h2>Evidências alteradas sem mudança de severidade</h2>' + table(evidence_changes, [("severity","Severidade"),("category","Categoria"),("workload","Workload"),("check","Check"),("before","Antes"),("after","Depois")])
        return self.layout("Comparar coletas", f'<h1>Comparar coletas</h1>{body}', directory, "compare")
    def collect_form(self, baseline: bool) -> str:
        detected = cluster()[1]
        eks_name = eks_cluster_name()
        prometheus_url = prometheus_url_suggestion()
        environment = f"Amazon EKS / {eks_name}" if eks_name else f"Kubernetes / {detected}"
        windows = "".join(
            f'<option value="{item}" {"selected" if item == "7d" else ""}>{item}</option>'
            for item in ("1d", "3d", "7d", "14d", "30d")
        )
        profiles = (
            '<option value="low-impact">Baixo impacto — máximo 15 min</option>'
            '<option value="conservative" selected>Conservador — máximo 30 min (recomendado)</option>'
            '<option value="exhaustive">Exaustivo — máximo 60 min</option>'
        )
        control = SUPERVISOR.status()
        control_html = ""
        if control.get("active"):
            control_html = (
                f'<div class="message warn">Coleta <b>{esc(control.get("collection"))}</b> em execução: '
                f'{esc(control.get("component"))}; restam no máximo {esc(control.get("remainingSeconds"))}s. '
                f'<form class="inline-action" method="post" action="/cancel"><input type="hidden" name="action_token" value="{ACTION_TOKEN}"><button class="button danger" type="submit">Cancelar agora</button></form></div>'
            )
        body = (
            f'<h1>{"Novo baseline" if baseline else "Nova coleta"}</h1>{control_html}'
            '<p>Assessment adaptativo e somente leitura. O perfil controla concorrência e orçamento, '
            'não muda os critérios. URL Prometheus é opcional, explícita e não pode conter credenciais.</p>'
            f'<form class="collect" id="collection-form" method="post" action="/collect">'
            f'<input type="hidden" name="action_token" value="{ACTION_TOKEN}">'
            f'<input type="hidden" name="baseline" value="{1 if baseline else 0}">'
            f'<label>Ambiente detectado<input value="{esc(environment)}" disabled></label>'
            '<label>Identificador da mudança<input name="label" value="manual" required></label>'
            f'<label>Perfil de coleta<select name="profile">{profiles}</select></label>'
            '<label>Namespace (vazio = cluster inteiro)<input name="namespace" placeholder="namespace opcional"></label>'
            '<label>Região AWS (opcional)<input name="region" placeholder="us-east-1"></label>'
            '<details class="form-section"><summary>Cloud Provider APIs — AKS/GKE (opcional)</summary>'
            '<label>AKS cluster<input name="aks_cluster" placeholder="nome do cluster"></label>'
            '<label>AKS resource group<input name="aks_resource_group" placeholder="resource group"></label>'
            '<label>GKE cluster<input name="gke_cluster" placeholder="nome do cluster"></label>'
            '<label>GKE location<input name="gke_location" placeholder="us-central1"></label>'
            '<label>GCP project<input name="gcp_project" placeholder="project usado somente na chamada; não persistido"></label></details>'
            '<label class="checkbox-row"><input type="checkbox" name="account_security" value="1"><span>Incluir GuardDuty/runtime security (requer permissão de conta)</span></label>'
            '<label class="checkbox-row"><input type="checkbox" name="include_logs" value="1"><span>Incluir logs sanitizados (opt-in; exige targets explícitos)</span></label>'
            '<label>Targets de logs (namespace/kind/name[:container], separados por vírgula)<input name="log_targets" placeholder="apps/deployment/minha-api:app"></label>'
            '<label>Namespace do Service Prometheus (opcional)<input name="prometheus_namespace" placeholder="informar explicitamente"></label>'
            '<label>Service Prometheus (opcional)<input name="prometheus_service" placeholder="informar explicitamente"></label>'
            f'<label>URL explícita do Prometheus (opcional)<input name="prometheus_url" value="{esc(prometheus_url)}" placeholder="http://prometheus.example:9090"></label>'
            f'<label>Janela histórica<select name="prometheus_window">{windows}</select></label>'
            '<section id="collection-progress" class="collection-progress" hidden aria-live="polite">'
            '<div class="progress-heading"><b id="progress-title">Preparando coleta</b><span id="progress-value">0%</span></div>'
            '<div class="progress-track" role="progressbar" aria-label="Progresso da coleta" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0"><span id="progress-fill"></span></div>'
            '<p id="progress-detail">Validando o ambiente...</p></section>'
            '<button id="collection-submit">Iniciar assessment read-only</button></form>'
            '<script>(()=>{const form=document.getElementById("collection-form");if(!form)return;const box=document.getElementById("collection-progress"),bar=box.querySelector("[role=progressbar]"),fill=document.getElementById("progress-fill"),value=document.getElementById("progress-value"),title=document.getElementById("progress-title"),detail=document.getElementById("progress-detail"),button=document.getElementById("collection-submit");let timer;const labels={preparing:"Preparando coleta",preflight:"Validando ambiente",assessment:"Executando assessment",discovery:"Coletando discovery",comprehensive:"Analisando recomendações",prometheus:"Coletando métricas do Prometheus","artifact-validation":"Validando artefatos"};function render(s){const p=Math.max(0,Math.min(100,Number(s.progressPercent||0)));fill.style.width=p+"%";value.textContent=p+"%";bar.setAttribute("aria-valuenow",String(p));title.textContent=s.status==="COMPLETED"?"Coleta concluída":(labels[s.component]||"Coleta em andamento");const done=(s.completedComponents||[]).length,total=(s.plannedComponents||[]).length;detail.textContent=s.active?`${done} de ${total} etapas concluídas${s.remainingSeconds!==undefined?` · até ${s.remainingSeconds}s restantes`:""}`:(s.status==="COMPLETED"?"Todos os artefatos foram gerados e validados.":`Coleta encerrada: ${s.status||"erro"}.`)}async function poll(){try{const r=await fetch("/api/collection-status",{cache:"no-store"});if(r.ok)render(await r.json())}catch(_){detail.textContent="Aguardando atualização do servidor..."}}form.addEventListener("submit",async e=>{e.preventDefault();box.hidden=false;button.disabled=true;button.textContent="Coleta em andamento...";render({progressPercent:0,component:"preflight",active:true,completedComponents:[],plannedComponents:[1],remainingSeconds:"..."});timer=setInterval(poll,750);try{const r=await fetch(form.action,{method:"POST",body:new URLSearchParams(new FormData(form)),headers:{"X-Assessment-Async":"1"}});const data=await r.json().catch(()=>({}));if(!r.ok)throw new Error(data.error||`Falha HTTP ${r.status}`);clearInterval(timer);render({progressPercent:100,status:"COMPLETED",active:false});window.location.assign(data.redirect)}catch(err){clearInterval(timer);await poll();button.disabled=false;button.textContent="Tentar novamente";detail.textContent=err.message}})})();</script>'
        )
        return self.layout("Nova coleta", body)
    def do_GET(self):
        if not self.authenticated(): return
        parsed = urlparse(self.path); path, query = parsed.path, parse_qs(parsed.query); directory = self.selected(query)
        if path == "/api/health": return self.send_json({"ok": True, "readOnly": True, "clusterName": cluster()[1]})
        if path == "/api/collections": return self.send_json([{"id": x.name, **metadata(x)} for x in self.directories()])
        if path == "/api/collection-status": return self.send_json(SUPERVISOR.status())
        if path == "/": return self.send_html(self.overview(directory))
        if path == "/search": return self.send_html(self.search_page(directory, query))
        if path == "/resources": return self.send_html(self.resources_page(directory, query))
        if path == "/problems": return self.send_html(self.problems(directory, query))
        if path == "/assessment": return self.send_html(self.assessment(directory))
        if path == "/workload": return self.send_html(self.workload(directory, query))
        if path == "/technologies": return self.send_html(self.technologies(directory))
        if path == "/capacity": return self.send_html(self.capacity(directory))
        if path == "/diagnostics": return self.send_html(self.diagnostics(directory))
        if path == "/node-health": return self.send_html(self.node_health(directory))
        if path == "/versions": return self.send_html(self.versions(directory))
        if path == "/manifest-quality": return self.send_html(self.manifest_quality(directory))
        if path == "/best-practices": return self.send_html(self.best_practices(directory, query))
        if path == "/logs": return self.send_html(self.logs(directory))
        if path == "/prometheus": return self.send_html(self.prometheus(directory))
        if path == "/cloud": return self.send_html(self.cloud_provider(directory))
        if path == "/aws": return self.send_html(self.aws_eks(directory))
        if path == "/cis-security": return self.send_html(self.cis_security(directory, query))
        if path == "/cis-report": return self.send_html(self.cis_report(directory))
        if path == "/coverage": return self.send_html(self.coverage(directory))
        if path == "/api-inventory": return self.send_html(self.api_inventory(directory, query))
        if path == "/compare": return self.send_html(self.compare(directory, query))
        if path == "/collect": return self.send_html(self.collect_form(query.get("baseline", ["0"])[0] == "1"))
        if path == "/export":
            if not directory: return self.send_json({"error": "Coleta não encontrada"}, 404)
            return self.send_json(details(directory), filename=f"{directory.name}.json")
        if path == "/export-cis":
            if not directory: return self.send_json({"error": "Coleta não encontrada"}, 404)
            report = details(directory).get("cisSecurity") or {}
            if not report: return self.send_json({"error": "Relatório CIS não disponível para esta coleta"}, 404)
            return self.send_json(report, filename=f"{directory.name}-cis-security.json")
        if path == "/export-operational":
            if not directory: return self.send_json({"error": "Coleta não encontrada"}, 404)
            report = details(directory).get("operationalInsights") or {}
            if not report: return self.send_json({"error": "Operational Insights não disponível"}, 404)
            return self.send_json(report, filename=f"{directory.name}-operational-insights.json")
        if path == "/export-cloud":
            if not directory: return self.send_json({"error": "Coleta não encontrada"}, 404)
            report = details(directory).get("cloudProvider") or {}
            if not report: return self.send_json({"error": "Evidência do cloud provider não disponível"}, 404)
            return self.send_json(report, filename=f"{directory.name}-cloud-provider.json")
        if path == "/manifests":
            if not directory: return self.send_json({"error": "Coleta não encontrada"}, 404)
            return self.send_json(jfile(directory / "application-manifests-sanitized.json", {}), filename=f"{directory.name}-manifests-sanitized.json")
        if path == "/styles.css":
            try:
                data = (self.static / "styles.css").read_bytes()
            except OSError:
                return self.send_error(503, "Dashboard stylesheet unavailable")
            self.send_response(200); self.send_header("Content-Type", "text/css; charset=utf-8"); self.send_header("Content-Length", str(len(data))); self.send_header("Cache-Control", "no-store"); self.end_headers(); return self.wfile.write(data)
        if path == "/kubernetes-logo.svg":
            try:
                data = (self.static / "kubernetes-logo.svg").read_bytes()
            except OSError:
                return self.send_error(503, "Kubernetes logo unavailable")
            self.send_response(200); self.send_header("Content-Type", "image/svg+xml"); self.send_header("Content-Length", str(len(data))); self.send_header("Cache-Control", "public, max-age=86400"); self.security_headers(); self.end_headers(); return self.wfile.write(data)
        return self.send_json({"error": "Rota não encontrada"}, 404)

    def do_POST(self):
        if not self.authenticated(): return
        path = urlparse(self.path).path
        if path not in {"/collect", "/cancel"}:
            return self.send_json({"error": "Rota não encontrada"}, 404)
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return self.send_json({"error": "Content-Length inválido"}, 400)
        if length > 65536:
            return self.send_json({"error": "Formulário excede o limite"}, 413)
        form = parse_qs(self.rfile.read(length).decode("utf-8"))
        supplied_token = form.get("action_token", [""])[0]
        local_request = self.client_address[0] in {"127.0.0.1", "::1"}
        if path == "/cancel":
            if not local_request and not secrets.compare_digest(supplied_token, ACTION_TOKEN):
                return self.send_json({"error": "Token de ação inválido"}, 403)
            SUPERVISOR.cancel("operator requested cancellation")
            self.send_response(303)
            self.send_header("Location", "/collect")
            self.end_headers()
            return
        if not secrets.compare_digest(supplied_token, ACTION_TOKEN):
            return self.send_json({"error": "Token de ação inválido"}, 403)
        if not LOCK.acquire(blocking=False):
            return self.send_html(
                self.layout("Coleta", '<div class="message bad">Já existe uma coleta em execução.</div>'),
                409,
            )
        async_request = self.headers.get("X-Assessment-Async", "") == "1"
        collection_started = False
        final_status = "FAILED"
        try:
            context, detected = cluster()
            label = re.sub(r"[^A-Za-z0-9._-]", "-", form.get("label", ["manual"])[0])[:64] or "manual"
            baseline = form.get("baseline", ["0"])[0] == "1"
            phase = "before" if baseline else "manual"
            profile = form.get("profile", ["conservative"])[0]
            profiles = {
                "low-impact": {"workers": "2", "delay": "250", "requests": "500", "response": "256", "duration": "900"},
                "conservative": {"workers": "4", "delay": "100", "requests": "1500", "response": "512", "duration": "1800"},
                "exhaustive": {"workers": "8", "delay": "25", "requests": "5000", "response": "1024", "duration": "3600"},
            }
            profile_values = profiles.get(profile, profiles["conservative"])
            namespace = form.get("namespace", [""])[0].strip()
            if namespace and not re.fullmatch(r"[a-z0-9]([-a-z0-9.]*[a-z0-9])?", namespace):
                return self.send_html(self.layout("Erro", '<div class="message bad">Namespace inválido.</div>'), 400)
            prometheus_url = form.get("prometheus_url", [""])[0].strip() or os.environ.get("PROMETHEUS_URL", "").strip()
            runtime_env = {
                **os.environ,
                "EKS_CLUSTER_NAME": eks_cluster_name(),
                "AWS_REGION": form.get("region", [""])[0].strip(),
                "AKS_CLUSTER_NAME": form.get("aks_cluster", [""])[0].strip(),
                "AKS_RESOURCE_GROUP": form.get("aks_resource_group", [""])[0].strip(),
                "GKE_CLUSTER_NAME": form.get("gke_cluster", [""])[0].strip(),
                "GKE_LOCATION": form.get("gke_location", [""])[0].strip(),
                "GCP_PROJECT": form.get("gcp_project", [""])[0].strip(),
                "PROMETHEUS_URL": prometheus_url,
                "PROMETHEUS_NAMESPACE": form.get("prometheus_namespace", [""])[0].strip(),
                "PROMETHEUS_SERVICE": form.get("prometheus_service", [""])[0].strip(),
                "PYTHON_BIN": sys.executable,
                "ASSESSMENT_NAMESPACE": namespace,
                "ASSESSMENT_INCLUDE_ACCOUNT_SECURITY": "1" if form.get("account_security", ["0"])[0] == "1" else "0",
                "ASSESSMENT_INCLUDE_LOGS": "1" if form.get("include_logs", ["0"])[0] == "1" else "0",
                "ASSESSMENT_LOG_TARGETS": form.get("log_targets", [""])[0].strip(),
            }
            stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            ident = f"eks-{stamp}-{phase}-{label}-{secrets.token_hex(4)}"
            planned_components = ["preflight", "assessment", "discovery", "inventory-services.json", "inventory-pvcs.json", "inventory-hpas.json"]
            if prometheus_url:
                planned_components.append("prometheus")
            planned_components.extend(["comprehensive", "artifact-validation"])
            max_duration = int(profile_values["duration"])
            SUPERVISOR.start(ident, max_duration, planned_components)
            collection_started = True
            preflight = SUPERVISOR.run(
                "preflight",
                ["bash", str(self.repository / "src/assessment-preflight.sh")],
                cwd=self.repository,
                env=runtime_env,
                timeout=60,
            )
            preflight_log = (preflight.stdout + preflight.stderr).strip()
            if preflight.returncode != 0:
                stopped = SUPERVISOR.status().get("stopKind")
                if stopped in {"CANCELLED", "TIMED_OUT"}:
                    finished = SUPERVISOR.finish(stopped)
                    output = self.root / ident
                    output.mkdir(parents=True, exist_ok=True)
                    interrupted_metadata = {
                        "id": ident, "createdAt": finished.get("startedAt") or utc_iso(),
                        "finishedAt": finished.get("finishedAt") or utc_iso(),
                        "clusterName": detected, "context": context, "baseline": baseline,
                        "profile": profile, "namespaceScope": namespace or "*",
                        "status": stopped, "completed": False,
                        "cancelled": stopped == "CANCELLED", "cancelReason": finished.get("reason") or None,
                        "maxDurationSeconds": max_duration, "readOnly": True,
                        "collectorComponents": ["preflight"], "collectorExitCodes": [preflight.returncode],
                        "performance": {"durationSeconds": finished.get("durationSeconds"), "componentDurationsSeconds": finished.get("componentDurationsSeconds") or {}},
                    }
                    (output / "metadata.json").write_text(json.dumps(interrupted_metadata, ensure_ascii=False, indent=2), encoding="utf-8")
                    (output / "preflight.log").write_text(preflight_log + "\n", encoding="utf-8")
                    if async_request:
                        return self.send_json({"error": f"Coleta {stopped.lower()}", "collection": ident}, 409)
                    return self.send_html(self.layout("Coleta interrompida", f'<div class="message warn">Coleta {esc(stopped)} durante o preflight. Estado parcial preservado em {esc(ident)}.</div>'), 409)
                SUPERVISOR.finish("FAILED")
                if async_request:
                    return self.send_json({"error": "Preflight falhou", "detail": preflight_log}, 503)
                return self.send_html(
                    self.layout("Preflight", f'<div class="message bad">Preflight falhou.</div><pre>{esc(preflight_log)}</pre>'),
                    503,
                )
            output = self.root / ident
            output.mkdir(parents=True)
            initial_metadata = {
                "id": ident,
                "createdAt": utc_iso(),
                "clusterName": detected,
                "eksClusterName": runtime_env["EKS_CLUSTER_NAME"] or None,
                "context": context,
                "baseline": baseline,
                "profile": profile,
                "namespaceScope": namespace or "*",
                "status": "RUNNING",
                "completed": False,
                "cancelled": False,
                "maxDurationSeconds": max_duration,
                "readOnly": True,
                "collectorComponents": ["preflight"],
                "collectorExitCodes": [0],
            }
            (output / "metadata.json").write_text(json.dumps(initial_metadata, ensure_ascii=False, indent=2), encoding="utf-8")
            (output / "preflight.log").write_text(preflight_log + "\n", encoding="utf-8")
            env = {**runtime_env, "OUTPUT_DIR": str(output)}
            codes: list[int] = [0]
            components: list[str] = ["preflight"]
            runs = [
                ("assessment", "assessment.log", ["bash", str(self.repository / "src/assess-eks.sh")], 900),
                ("discovery", "discovery.log", ["bash", str(self.repository / "src/eks-cluster-discovery.sh"), "--output-dir", str(output / "discovery"), "--combined-report"] + (["--namespace", namespace] if namespace else []), 1200),
            ]
            for component, logfile, args, timeout in runs:
                result = SUPERVISOR.run(component, args, cwd=self.repository, env=env, timeout=timeout)
                (output / logfile).write_text(result.stdout + result.stderr, encoding="utf-8")
                components.append(component)
                codes.append(result.returncode)
            inventory_scope = ["-n", namespace] if namespace else ["-A"]
            for filename, args in {
                "services.json": ["kubectl", "get", "services", *inventory_scope, "-o", "json"],
                "pvcs.json": ["kubectl", "get", "pvc", *inventory_scope, "-o", "json"],
                "hpas.json": ["kubectl", "get", "hpa", *inventory_scope, "-o", "json"],
            }.items():
                result = SUPERVISOR.run(f"inventory-{filename}", args, timeout=120)
                try:
                    inventory_payload = json.loads(result.stdout) if result.returncode == 0 else {"items": []}
                except json.JSONDecodeError:
                    inventory_payload = {"items": []}
                (output / filename).write_text(
                    json.dumps(sanitize_snapshot_tree(inventory_payload), ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            window = form.get("prometheus_window", [os.environ.get("PROMETHEUS_WINDOW", "7d")])[0]
            if window not in {"1d", "3d", "7d", "14d", "30d"}:
                window = "7d"
            telemetry_path = output / "prometheus-telemetry.json"
            if prometheus_url:
                command = [
                    sys.executable,
                    str(self.repository / "src/prometheus_telemetry.py"),
                    "--url", prometheus_url,
                    "--window", window,
                    "--workloads-file", str(output / "workloads.json"),
                    "--workers", profile_values["workers"],
                ]
                result = SUPERVISOR.run("prometheus", command, cwd=self.repository, env=env, timeout=1800)
                telemetry_path.write_text(
                    result.stdout or json.dumps({"state": "UNAVAILABLE", "reason": result.stderr}),
                    encoding="utf-8",
                )
                (output / "prometheus-telemetry.log").write_text(result.stderr, encoding="utf-8")
                components.append("prometheus")
                codes.append(result.returncode)
            else:
                telemetry_path.write_text(
                    json.dumps(
                        {"state": "DISABLED", "reason": "PROMETHEUS_URL não configurada explicitamente", "workloads": []},
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
            scanner_command = [
                sys.executable,
                str(self.repository / "src/eks_comprehensive_assessment.py"),
                "--snapshot-dir", str(output),
                "--collect-live",
                "--timeout", "30",
                "--chunk-size", "200",
                "--inventory-workers", profile_values["workers"],
                "--api-delay-ms", profile_values["delay"],
                "--max-requests", profile_values["requests"],
                "--max-duration", profile_values["duration"],
                "--max-response-mb", profile_values["response"],
            ]
            if namespace:
                scanner_command.extend(["--namespace", namespace])
            scanner = SUPERVISOR.run("comprehensive", scanner_command, cwd=self.repository, env=env, timeout=max_duration)
            (output / "comprehensive-assessment.log").write_text(scanner.stdout + scanner.stderr, encoding="utf-8")
            components.append("comprehensive")
            codes.append(scanner.returncode)
            control = SUPERVISOR.status()
            interim_status = control.get("stopKind") or ("FAILED" if any(codes) else "RUNNING")
            value = {
                "id": ident,
                "createdAt": initial_metadata["createdAt"],
                "clusterName": detected,
                "eksClusterName": eks_cluster_name() or None,
                "context": context,
                "baseline": baseline,
                "profile": profile,
                "namespaceScope": namespace or "*",
                "status": interim_status,
                "completed": False,
                "cancelled": interim_status == "CANCELLED",
                "cancelReason": control.get("reason") or None,
                "maxDurationSeconds": max_duration,
                "readOnly": True,
                "collectorComponents": components,
                "collectorExitCodes": codes,
            }
            (output / "metadata.json").write_text(
                json.dumps(value, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            validator = SUPERVISOR.run(
                "artifact-validation",
                [sys.executable, str(self.repository / "src/validate_assessment_artifacts.py"), str(output)],
                cwd=self.repository,
                env=env,
                timeout=300,
            )
            (output / "artifact-smoke.log").write_text(validator.stdout + validator.stderr, encoding="utf-8")
            components.append("smoke")
            codes.append(validator.returncode)
            control = SUPERVISOR.status()
            final_status = control.get("stopKind") or ("FAILED" if any(codes) else "COMPLETED")
            value["collectorComponents"] = components
            value["collectorExitCodes"] = codes
            value["status"] = final_status
            value["completed"] = final_status == "COMPLETED"
            value["cancelled"] = final_status == "CANCELLED"
            value["cancelReason"] = control.get("reason") or None
            value["finishedAt"] = utc_iso()
            finished_control = SUPERVISOR.finish(final_status)
            comprehensive_value = jfile(output / "comprehensive-assessment.json", {})
            value["performance"] = {
                "durationSeconds": finished_control.get("durationSeconds"),
                "componentDurationsSeconds": finished_control.get("componentDurationsSeconds") or {},
                "scanner": comprehensive_value.get("performance") or {},
            }
            (output / "metadata.json").write_text(
                json.dumps(value, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            if async_request:
                return self.send_json({"ok": True, "collection": ident, "redirect": f"/?collection={ident}"})
            self.send_response(303)
            self.send_header("Location", f"/?collection={ident}")
            self.end_headers()
        except Exception as error:
            control = SUPERVISOR.status()
            final_status = control.get("stopKind") or "FAILED"
            if async_request:
                self.send_json({"error": str(error)}, 500)
            else:
                self.send_html(self.layout("Erro", f'<div class="message bad">{esc(error)}</div>'), 500)
        finally:
            if collection_started and SUPERVISOR.status().get("active"):
                SUPERVISOR.finish(final_status)
            LOCK.release()

def utc_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def main():
    parser = argparse.ArgumentParser(); parser.add_argument("--root", required=True, type=Path); parser.add_argument("--static", required=True, type=Path); parser.add_argument("--host", default="127.0.0.1"); parser.add_argument("--port", type=int, default=8765); parser.add_argument("--allow-remote", action="store_true"); parser.add_argument("--access-token", default=""); args = parser.parse_args()
    if args.host not in {"127.0.0.1", "::1", "localhost"} and not args.allow_remote:
        parser.error("non-loopback binding requires explicit --allow-remote")
    if args.host not in {"127.0.0.1", "::1", "localhost"} and len(args.access_token) < 32:
        parser.error("non-loopback binding requires an access token with at least 32 characters")
    Handler.root = args.root.resolve(); Handler.static = args.static.resolve(); Handler.repository = Path(__file__).resolve().parents[1]; Handler.root.mkdir(parents=True, exist_ok=True)
    Handler.access_token = args.access_token
    if not (Handler.static / "styles.css").is_file():
        parser.error(f"dashboard stylesheet not found: {Handler.static / 'styles.css'}")
    if not (Handler.static / "kubernetes-logo.svg").is_file():
        parser.error(f"Kubernetes logo not found: {Handler.static / 'kubernetes-logo.svg'}")
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.timeout = 5
    server.daemon_threads = True

    def shutdown_signal(_signum, _frame):
        SUPERVISOR.cancel("dashboard received termination signal")
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, shutdown_signal)
    print(f"EKS dashboard Python: http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        SUPERVISOR.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
