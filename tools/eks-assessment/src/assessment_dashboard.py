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
from urllib.parse import parse_qs, quote_plus, urlencode, urlparse

from assessment_process_supervisor import CollectionSupervisor
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
    return {"id": directory.name, "metadata": metadata(directory), "summary": summary, "resources": resources, "findings": findings, "metrics": tsv(directory / "prometheus-baseline.tsv"), "telemetry": jfile(directory / "prometheus-telemetry.json", {"state": "DISABLED"}), "discovery": jfile(directory / "discovery" / "summary.json", None), "comprehensive": comprehensive, "awsEks": jfile(directory / "aws-eks-assessment.json", comprehensive.get("awsEks", {"state": "UNKNOWN"})), "cisSecurity": jfile(directory / "cis-security-assessment.json", comprehensive.get("cisSecurity", {})), "technologies": comprehensive.get("technologies", []), "capacity": comprehensive.get("capacityRecommendations", []), "coverage": (comprehensive.get("collection") or {}).get("resources", {}), "universal": jfile(directory / "universal-inventory.json", {"resources": []})}


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
        links = [("overview", "/", "Visão geral"), ("assessment", "/assessment", "Assessment"), ("problems", "/problems", "Problemas"), ("cis", "/cis-security", "CIS Security"), ("nodes", "/resources?kind=nodes", "Nodes"), ("namespaces", "/resources?kind=namespaces", "Namespaces"), ("workloads", "/resources?kind=workloads", "Workloads"), ("technologies", "/technologies", "Tecnologias"), ("capacity", "/capacity", "Capacidade"), ("rabbitmq", "/resources?kind=rabbitmq", "RabbitMQ"), ("prometheus", "/prometheus", "Prometheus"), ("aws", "/aws", "AWS / EKS"), ("coverage", "/coverage", "Cobertura"), ("compare", "/compare", "Comparar coletas")]
        nav = []
        for key, path, label in links:
            separator = "&" if "?" in path else "?"
            href = path + (separator + cq if cq else "")
            nav.append(f'<a class="tab {"active" if key == active else ""}" href="{href}">{esc(label)}</a>')
        options = "".join(f'<option value="{esc(x.name)}" {"selected" if directory and x == directory else ""}>{esc(x.name)}{" • baseline" if metadata(x).get("baseline") else ""}</option>' for x in self.directories()) or '<option>Nenhuma coleta</option>'
        picker = f'<form class="picker" method="get"><label>Coleta<select name="collection">{options}</select></label><button>Carregar</button></form>'
        control = SUPERVISOR.status()
        actions = f'<a class="button" href="/?{cq}">Atualizar tela</a><a class="button" href="/collect">Coletar agora</a><a class="button" href="/collect?baseline=1">Novo baseline</a>'
        if control.get("active"):
            actions += f'<form class="inline-action" method="post" action="/cancel"><input type="hidden" name="action_token" value="{ACTION_TOKEN}"><button class="button danger" type="submit">Cancelar coleta</button></form>'
        if directory: actions += f'<a class="button" href="/export?{cq}">Exportar</a>'
        return f'<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)}</title><link rel="stylesheet" href="/styles.css"></head><body><header><div class="environment">AMBIENTE DE ASSESSMENT <span>{esc(value.get("clusterName"))}</span></div><div class="top"><strong>EKS <em>ENVIRONMENT</em></strong><span class="health">READ-ONLY</span><span class="headline">{esc(ident or "sem coleta")}</span><div class="actions">{actions}</div></div></header><main><aside>{"".join(nav)}</aside><section class="content">{picker}{body}</section></main></body></html>'

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
        priority = table(findings[:20], [("severity", "Severidade"), ("category", "Categoria"), ("namespace", "Namespace"), ("workload", "Workload"), ("check", "Check"), ("detail", "Evidência"), ("recommendation", "Recomendação")])
        body = f'<section class="state"><small>SAÚDE DO AMBIENTE</small><h1>{state}</h1><p>{critical} crítico(s), {warnings} alerta(s), {unknown} desconhecido(s), {partial} parcial(is), {scanner.get("passed",0)} conforme(s), {scanner.get("notApplicable",0)} N/A. {scanner.get("checks","N/A")} checks em {scanner.get("workloads","N/A")} workloads / {scanner.get("containers","N/A")} containers. Discovery {discovery.get("succeeded","N/A")}/{discovery.get("sections","N/A")}.</p></section><div class="cards">{card_html}</div><h2>Problemas e recomendações prioritárias</h2>{priority}'
        return self.layout("Visão geral", body, directory, "overview")

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
        rendered = []
        for row in rows:
            copy = dict(row)
            if row.get("kind") in WORKLOAD_KINDS:
                params = urlencode({"collection": directory.name, "namespace": row.get("namespace", ""), "kind": row.get("kind", ""), "name": row.get("name", "")})
                copy["nameHtml"] = f'<a class="resource-link" href="/workload?{params}">{esc(row.get("name"))}</a>'
            else: copy["nameHtml"] = esc(row.get("name"))
            rendered.append(copy)
        columns = [("kind", "Kind"), ("namespace", "Namespace"), ("nameHtml", "Nome"), ("status", "Status"), ("ready", "Ready"), ("node", "Node"), ("restarts", "Restarts"), ("detail", "Detalhe")]
        active = "rabbitmq" if kind == "rabbitmq" else "nodes" if kind == "nodes" else "namespaces" if kind == "namespaces" else "workloads"
        return self.layout(kind.upper(), f'<h1>{esc(kind.upper())} <small>{len(rows)} item(ns)</small></h1>{filters}{table(rendered, columns, {"nameHtml"})}', directory, active)

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

    def capacity(self, directory: Path | None) -> str:
        if not directory: return self.overview(None)
        rows = []
        for item in details(directory)["capacity"]:
            rows.append({"namespace": item.get("namespace"), "workload": item.get("workload"), "window": item.get("window"), "replicas": item.get("replicasObserved"), "confidence": item.get("confidence"), "currentCpuRequest": (item.get("current") or {}).get("cpuRequestPerReplica"), "currentCpuLimit": (item.get("current") or {}).get("cpuLimitPerReplica"), "cpuRequest": (item.get("cpu") or {}).get("requestPerReplica"), "cpuLimit": (item.get("cpu") or {}).get("limitPerReplica"), "currentMemoryRequest": (item.get("current") or {}).get("memoryRequestPerReplica"), "currentMemoryLimit": (item.get("current") or {}).get("memoryLimitPerReplica"), "memoryRequest": (item.get("memory") or {}).get("requestPerReplica"), "memoryLimit": (item.get("memory") or {}).get("limitPerReplica"), "assessment": "; ".join(item.get("assessment") or []), "scaling": item.get("scalingRecommendation"), "caveat": item.get("caveat")})
        message = '<div class="message">Nenhuma proposta foi produzida. Configure uma URL Prometheus explícita e confirme séries de CPU e memória. Ausência de métricas não significa conformidade.</div>' if not rows else '<div class="message good">Propostas estatísticas: valide startup, sazonalidade, atribuição por container e throttling antes de alterar manifests.</div>'
        columns = [("namespace", "Namespace"), ("workload", "Workload"), ("window", "Janela"), ("replicas", "Réplicas"), ("confidence", "Confiança"), ("currentCpuRequest", "CPU req atual"), ("cpuRequest", "CPU req proposta"), ("currentCpuLimit", "CPU lim atual"), ("cpuLimit", "CPU lim proposta"), ("currentMemoryRequest", "Mem req atual"), ("memoryRequest", "Mem req proposta"), ("currentMemoryLimit", "Mem lim atual"), ("memoryLimit", "Mem lim proposta"), ("assessment", "Diagnóstico"), ("scaling", "HPA/KEDA"), ("caveat", "Ressalva")]
        return self.layout("Capacidade", f'<h1>Requests/limits orientados por telemetria</h1>{message}{table(rows,columns)}', directory, "capacity")

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

    def cis_security(self, directory: Path | None) -> str:
        if not directory:
            return self.overview(None)
        report = details(directory).get("cisSecurity") or {}
        if not report:
            return self.layout("CIS Security", '<h1>CIS Security</h1><div class="message">Esta coleta é anterior à geração do artefato CIS Security. Execute uma nova coleta.</div>', directory, "cis")
        summary = report.get("summary") or {}
        rows = []
        for item in report.get("controls") or []:
            evidence = json.dumps(item.get("evidence") or {}, ensure_ascii=False, sort_keys=True)
            rows.append({**item, "evidenceText": evidence[:800] + ("..." if len(evidence) > 800 else "")})
        score = summary.get("scorePercent")
        facts = (
            '<div class="facts">'
            f'<div><small>Plataforma</small><b>{esc(report.get("platform", "UNKNOWN"))}</b></div>'
            f'<div><small>Controles</small><b>{summary.get("controls", 0)}</b></div>'
            f'<div><small>Avaliados no score</small><b>{summary.get("scored", 0)}</b></div>'
            f'<div><small>PASS</small><b>{summary.get("passed", 0)}</b></div>'
            f'<div><small>WARN</small><b>{summary.get("warnings", 0)}</b></div>'
            f'<div><small>Score evidenciável</small><b>{esc(str(score) + "%" if score is not None else "N/A")}</b></div>'
            '</div>'
        )
        body = (
            f'<h1>CIS Security</h1><div class="message warn"><b>{esc(report.get("notice"))}</b> '
            'Controles gerenciados pelo provider, revisões manuais e evidência indisponível não reduzem artificialmente o score.</div>'
            f'{facts}<h2>Controles por evidência e responsabilidade</h2>'
            f'{table(rows, [("controlId","Control ID"),("title","Controle"),("evidenceSource","Evidence Source"),("applicability","Aplicabilidade"),("assessmentMode","Modo"),("managedResponsibility","Responsabilidade"),("status","Status"),("evidenceText","Evidência"),("recommendation","Recomendação")])}'
        )
        return self.layout("CIS Security", body, directory, "cis")

    def coverage(self, directory: Path | None) -> str:
        if not directory: return self.overview(None)
        value = details(directory)
        rows = [{"resource": key, "state": entry.get("state"), "count": entry.get("count", 0), "api": entry.get("resource", "-"), "reason": entry.get("reason", "")} for key, entry in sorted(value["coverage"].items())]
        discovery = value.get("discovery") or {}; universal = value.get("universal") or {}
        universal_rows = []
        for entry in universal.get("resources") or []:
            params = urlencode({"collection": directory.name, "resource": entry.get("resource", "")})
            universal_rows.append({"resourceHtml": f'<a class="resource-link" href="/api-inventory?{params}">{esc(entry.get("resource"))}</a>', "scope": entry.get("scope"), "state": entry.get("state"), "count": entry.get("count", 0), "mode": "profunda" if entry.get("deepCollected") else "identidade segura", "reason": entry.get("reason", "")})
        message = f'<div class="message good">Somente leitura. Secrets: metadados/chaves, sem valores. Discovery: {discovery.get("succeeded","N/A")} concluídas, {discovery.get("not_applicable","N/A")} N/A, {discovery.get("unavailable","N/A")} indisponíveis. Inventário universal: {universal.get("resourceTypes",0)} APIs e {universal.get("objectCount",0)} objetos; indisponíveis: {universal.get("unavailableResourceTypes",0)}.</div>'
        known = table(rows, [("resource", "Domínio profundo"), ("state", "Estado"), ("count", "Objetos"), ("api", "API usada"), ("reason", "Detalhe")])
        all_apis = table(universal_rows, [("resourceHtml", "API/recurso"), ("scope", "Escopo"), ("state", "Estado"), ("count", "Objetos"), ("mode", "Coleta"), ("reason", "Detalhe")], {"resourceHtml"})
        return self.layout("Cobertura", f'<h1>Cobertura da descoberta</h1>{message}<h2>Domínios com análise profunda</h2>{known}<h2>Todas as APIs listáveis</h2>{all_apis}', directory, "coverage")

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
            '<label class="checkbox-row"><input type="checkbox" name="account_security" value="1"><span>Incluir GuardDuty/runtime security (requer permissão de conta)</span></label>'
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
        if path == "/resources": return self.send_html(self.resources_page(directory, query))
        if path == "/problems": return self.send_html(self.problems(directory, query))
        if path == "/assessment": return self.send_html(self.assessment(directory))
        if path == "/workload": return self.send_html(self.workload(directory, query))
        if path == "/technologies": return self.send_html(self.technologies(directory))
        if path == "/capacity": return self.send_html(self.capacity(directory))
        if path == "/prometheus": return self.send_html(self.prometheus(directory))
        if path == "/aws": return self.send_html(self.aws_eks(directory))
        if path == "/cis-security": return self.send_html(self.cis_security(directory))
        if path == "/coverage": return self.send_html(self.coverage(directory))
        if path == "/api-inventory": return self.send_html(self.api_inventory(directory, query))
        if path == "/compare": return self.send_html(self.compare(directory, query))
        if path == "/collect": return self.send_html(self.collect_form(query.get("baseline", ["0"])[0] == "1"))
        if path == "/export":
            if not directory: return self.send_json({"error": "Coleta não encontrada"}, 404)
            return self.send_json(details(directory), filename=f"{directory.name}.json")
        if path == "/manifests":
            if not directory: return self.send_json({"error": "Coleta não encontrada"}, 404)
            return self.send_json(jfile(directory / "application-manifests-sanitized.json", {}), filename=f"{directory.name}-manifests-sanitized.json")
        if path == "/styles.css":
            try:
                data = (self.static / "styles.css").read_bytes()
            except OSError:
                return self.send_error(503, "Dashboard stylesheet unavailable")
            self.send_response(200); self.send_header("Content-Type", "text/css; charset=utf-8"); self.send_header("Content-Length", str(len(data))); self.send_header("Cache-Control", "no-store"); self.end_headers(); return self.wfile.write(data)
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
                "PROMETHEUS_URL": prometheus_url,
                "PROMETHEUS_NAMESPACE": form.get("prometheus_namespace", [""])[0].strip(),
                "PROMETHEUS_SERVICE": form.get("prometheus_service", [""])[0].strip(),
                "PYTHON_BIN": sys.executable,
                "ASSESSMENT_NAMESPACE": namespace,
                "ASSESSMENT_INCLUDE_ACCOUNT_SECURITY": "1" if form.get("account_security", ["0"])[0] == "1" else "0",
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
            SUPERVISOR.finish(final_status)
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
