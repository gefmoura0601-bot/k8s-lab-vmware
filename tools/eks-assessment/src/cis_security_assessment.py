#!/usr/bin/env python3
"""Evidence-driven CIS-based posture checks without node or control-plane access."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

CIS_REFERENCE = "https://www.cisecurity.org/benchmark/kubernetes"
DOMAIN_LABELS = {
    "rbac": "RBAC e identidade", "service-account": "RBAC e identidade",
    "pod": "Pod Security", "image": "Supply Chain", "network": "Network Security",
    "admission": "Admission Control", "control-plane": "Control Plane", "node": "Nodes",
    "aws": "Cloud Provider",
}
HIGH_RISK = {"cis.k8s.rbac.wildcards", "cis.k8s.rbac.cluster-admin", "cis.k8s.rbac.impersonation", "cis.k8s.pod.privileged", "cis.k8s.pod.host-namespaces"}
MEDIUM_RISK = {"cis.k8s.rbac.secrets", "cis.k8s.pod.capabilities", "cis.k8s.pod.privilege-escalation", "cis.k8s.image.digest", "cis.k8s.network.external-services", "cis.k8s.admission.policy-enforcement"}


def items(value: Any) -> list[dict[str, Any]]:
    return [item for item in (value or {}).get("items", []) if isinstance(item, dict)] if isinstance(value, dict) else []


def workload_specs(workloads: Iterable[dict[str, Any]], pods: Iterable[dict[str, Any]]) -> list[tuple[str, str, str, dict[str, Any]]]:
    result: list[tuple[str, str, str, dict[str, Any]]] = []
    for obj in [*workloads, *pods]:
        meta, spec = obj.get("metadata") or {}, obj.get("spec") or {}
        kind = str(obj.get("kind") or "Workload")
        if kind == "Pod" and any((owner.get("controller") is True) for owner in meta.get("ownerReferences") or []):
            continue
        if kind in {"Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job"}:
            spec = ((spec.get("template") or {}).get("spec") or {})
        elif kind == "CronJob":
            spec = (((spec.get("jobTemplate") or {}).get("spec") or {}).get("template") or {}).get("spec") or {}
        result.append((str(meta.get("namespace") or "default"), str(meta.get("name") or "unknown"), kind, spec))
    return result


def control(control_id: str, title: str, *, applicability: str = "APPLICABLE", mode: str = "AUTOMATED",
            status: str = "UNKNOWN", responsibility: str = "CUSTOMER", source: str = "KubernetesAPI",
            evidence: dict[str, Any] | None = None, recommendation: str = "") -> dict[str, Any]:
    return {
        "controlId": control_id, "title": title, "profile": "generic-kubernetes",
        "evidenceSource": source, "applicability": applicability, "assessmentMode": mode,
        "status": status, "managedResponsibility": responsibility,
        "evidence": evidence or {}, "recommendation": recommendation, "reference": CIS_REFERENCE,
    }


def unavailable(control_id: str, title: str, source: str, reason: str) -> dict[str, Any]:
    return control(control_id, title, applicability="EVIDENCE_UNAVAILABLE", status="UNKNOWN",
                   source=source, evidence={"reason": reason}, recommendation="Conceder somente a evidência read-only necessária ou registrar revisão manual.")


def coverage_available(collection: dict[str, Any], key: str) -> bool:
    return ((collection.get("resources") or {}).get(key) or {}).get("state") == "AVAILABLE"


def enrich_control(value: dict[str, Any]) -> dict[str, Any]:
    control_id = str(value.get("controlId") or "")
    segment = control_id.split(".")[2] if len(control_id.split(".")) > 2 else "other"
    weight = 3 if control_id in HIGH_RISK else 2 if control_id in MEDIUM_RISK else 1
    effort = "HIGH" if segment in {"control-plane", "node"} else "MEDIUM" if segment in {"rbac", "admission", "network"} else "LOW"
    validation = {
        "rbac": "kubectl get roles,rolebindings,clusterroles,clusterrolebindings -A -o json",
        "service-account": "kubectl get serviceaccounts,pods -A -o json",
        "pod": "kubectl get deployments,statefulsets,daemonsets,jobs,cronjobs,pods -A -o json",
        "image": "kubectl get deployments,statefulsets,daemonsets,jobs,cronjobs,pods -A -o json",
        "network": "kubectl get networkpolicies,services -A -o json",
        "admission": "kubectl get validatingwebhookconfigurations,mutatingwebhookconfigurations -o json",
        "control-plane": "Revisar evidência read-only do provider ou configuração sanitizada do control plane",
        "node": "Revisar evidência sanitizada e autorizada da configuração do kubelet",
    }.get(segment, "kubectl api-resources --verbs=list")
    value.update({
        "domain": DOMAIN_LABELS.get(segment, "Kubernetes Security"), "riskWeight": weight,
        "priority": ("P0" if weight == 3 else "P1" if weight == 2 else "P2") if value.get("status") == "WARN" else "NONE",
        "effort": effort, "impact": "HIGH" if weight == 3 else "MEDIUM" if weight == 2 else "LOW",
        "validationCommand": validation,
        "remediationExample": "Exemplo declarativo para revisão: ajuste o manifest/policy correspondente; não execute apply sem change review e testes.",
    })
    return value


def summarize(controls: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [c for c in controls if c["applicability"] == "APPLICABLE" and c["assessmentMode"] == "AUTOMATED" and c["managedResponsibility"] in {"CUSTOMER", "SHARED"}]
    customer_relevant = [c for c in controls if c["managedResponsibility"] in {"CUSTOMER", "SHARED"} and c["applicability"] not in {"NOT_APPLICABLE", "MANAGED_PROVIDER"}]
    passed_weight = sum(int(c["riskWeight"]) for c in scored if c["status"] == "PASS")
    total_weight = sum(int(c["riskWeight"]) for c in scored)
    domains = []
    for domain in sorted({str(c["domain"]) for c in controls}):
        domain_controls = [c for c in scored if c["domain"] == domain]
        domain_total = sum(int(c["riskWeight"]) for c in domain_controls)
        domain_pass = sum(int(c["riskWeight"]) for c in domain_controls if c["status"] == "PASS")
        domains.append({"domain": domain, "controls": len(domain_controls), "passed": sum(c["status"] == "PASS" for c in domain_controls), "scorePercent": round(domain_pass * 100 / domain_total) if domain_total else None})
    return {
        "controls": len(controls), "scored": len(scored), "passed": sum(c["status"] == "PASS" for c in scored),
        "warnings": sum(c["status"] == "WARN" for c in scored),
        "scorePercent": round(sum(c["status"] == "PASS" for c in scored) * 100 / len(scored)) if scored else None,
        "postureScorePercent": round(passed_weight * 100 / total_weight) if total_weight else None,
        "evidenceCoveragePercent": round(len(scored) * 100 / len(customer_relevant)) if customer_relevant else None,
        "weightedPoints": {"passed": passed_weight, "total": total_weight}, "domains": domains,
        "applicability": dict(Counter(c["applicability"] for c in controls)), "responsibility": dict(Counter(c["managedResponsibility"] for c in controls)),
    }


def compare_reports(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    old = {str(c.get("controlId")): c for c in before.get("controls", [])}
    new = {str(c.get("controlId")): c for c in after.get("controls", [])}
    changes = []
    for control_id in sorted(old.keys() | new.keys()):
        previous, current = old.get(control_id), new.get(control_id)
        if previous is None:
            kind = "NEW_CONTROL"
        elif current is None:
            kind = "REMOVED_CONTROL"
        elif previous.get("applicability") == "APPLICABLE" and current.get("applicability") in {"EVIDENCE_UNAVAILABLE", "MANUAL_REVIEW"}:
            kind = "EVIDENCE_LOSS"
        elif previous.get("applicability") in {"EVIDENCE_UNAVAILABLE", "MANUAL_REVIEW"} and current.get("applicability") == "APPLICABLE":
            kind = "COVERAGE_GAIN"
        elif previous.get("status") == "WARN" and current.get("status") == "PASS":
            kind = "RESOLVED"
        elif previous.get("status") == "PASS" and current.get("status") == "WARN":
            kind = "REGRESSION"
        elif previous.get("applicability") != current.get("applicability") or previous.get("managedResponsibility") != current.get("managedResponsibility"):
            kind = "RESPONSIBILITY_CHANGE"
        elif previous.get("status") != current.get("status"):
            kind = "STATUS_CHANGE"
        else:
            continue
        item = current or previous or {}
        changes.append({"controlId": control_id, "title": item.get("title"), "domain": item.get("domain"), "change": kind, "beforeStatus": (previous or {}).get("status"), "afterStatus": (current or {}).get("status"), "beforeApplicability": (previous or {}).get("applicability"), "afterApplicability": (current or {}).get("applicability")})
    old_summary, new_summary = before.get("summary") or {}, after.get("summary") or {}
    old_posture = old_summary.get("postureScorePercent", old_summary.get("scorePercent")) or 0
    new_posture = new_summary.get("postureScorePercent", new_summary.get("scorePercent")) or 0
    return {"before": old_summary, "after": new_summary, "postureDelta": new_posture - old_posture, "coverageDelta": (new_summary.get("evidenceCoveragePercent") or 0) - (old_summary.get("evidenceCoveragePercent") or 0), "counts": dict(Counter(c["change"] for c in changes)), "changes": changes}


def external_evidence(directory: Path) -> tuple[list[dict[str, Any]], list[str]]:
    path = directory / "cis-external-evidence.json"
    if not path.is_file():
        return [], []
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [], ["cis-external-evidence.json inválido"]
    accepted, errors = [], []
    now = dt.datetime.now(dt.timezone.utc)
    for entry in document.get("evidence", []) if isinstance(document, dict) else []:
        payload = entry.get("payload") or {}
        digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        try:
            expires = dt.datetime.fromisoformat(str(entry.get("validUntil", "")).replace("Z", "+00:00"))
        except ValueError:
            expires = now - dt.timedelta(seconds=1)
        if digest != entry.get("sha256") or expires <= now or entry.get("evidenceSource") not in {"NodeEvidence", "ControlPlaneEvidence", "ManualEvidence", "CloudProviderAPI"}:
            errors.append(str(entry.get("controlId") or "unknown"))
            continue
        accepted.append(entry)
    return accepted, errors


def apply_external_and_lifecycle(controls: list[dict[str, Any]], directory: Path, aws_eks: dict[str, Any] | None, provider: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    by_id = {str(item.get("controlId")): item for item in controls}
    evidence, errors = external_evidence(directory)
    for entry in evidence:
        target = by_id.get(str(entry.get("controlId")))
        if not target:
            target = control(str(entry.get("controlId")), str(entry.get("title") or entry.get("controlId")), responsibility=str(entry.get("managedResponsibility") or "SHARED"), source=entry["evidenceSource"], recommendation=str(entry.get("recommendation") or "Revisar evidência do cloud provider."))
            target["profile"] = str(entry.get("profile") or "provider-kubernetes")
            controls.append(target); by_id[str(entry.get("controlId"))] = target
        target.update({"evidenceSource": entry["evidenceSource"], "applicability": "APPLICABLE", "assessmentMode": "MANUAL" if entry["evidenceSource"] == "ManualEvidence" else "AUTOMATED", "status": entry.get("status", "UNKNOWN"), "evidence": entry.get("payload") or {}, "reviewedBy": entry.get("reviewedBy"), "validUntil": entry.get("validUntil")})
    if provider == "AWS" and isinstance(aws_eks, dict):
        for finding in aws_eks.get("findings") or []:
            rule_id = str(finding.get("ruleId") or "")
            if not rule_id.startswith("eks."):
                continue
            status = "PASS" if finding.get("severity") == "PASS" else "WARN" if finding.get("severity") in {"WARN", "CRIT"} else "UNKNOWN"
            controls.append(control(f"cis.aws.{rule_id}", str(finding.get("check") or rule_id), status=status, responsibility="SHARED", source="CloudProviderAPI", evidence={"detail": finding.get("detail")}, recommendation=str(finding.get("recommendation") or "Revisar configuração EKS.")))
    lifecycle_path = directory / "cis-remediation-state.json"
    lifecycle = {}
    if lifecycle_path.is_file():
        try: lifecycle = json.loads(lifecycle_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError): errors.append("cis-remediation-state.json inválido")
    states = lifecycle.get("controls", {}) if isinstance(lifecycle, dict) else {}
    for item in controls:
        state = states.get(item.get("controlId"), {}) if isinstance(states, dict) else {}
        item["remediation"] = {key: state.get(key) for key in ("owner", "dueDate", "state", "ticket", "justification", "riskAcceptedUntil") if state.get(key)}
    return controls, {"acceptedExternalEvidence": len(evidence), "evidenceErrors": errors, "lifecycleEntries": len(states)}


def assess(raw: dict[str, Any], base: dict[str, Any], collection: dict[str, Any], directory: Path,
           aws_eks: dict[str, Any] | None = None) -> dict[str, Any]:
    controls: list[dict[str, Any]] = []
    specs = workload_specs(items(base.get("workloads")), items(base.get("pods")))

    if coverage_available(collection, "roles") and coverage_available(collection, "clusterroles"):
        offenders = []
        for role in items(raw.get("roles")) + items(raw.get("clusterroles")):
            if any("*" in (rule.get("verbs") or []) or "*" in (rule.get("resources") or []) for rule in role.get("rules") or []):
                meta = role.get("metadata") or {}; offenders.append(f"{role.get('kind','Role')}/{meta.get('namespace','-')}/{meta.get('name','unknown')}")
        controls.append(control("cis.k8s.rbac.wildcards", "RBAC sem permissões wildcard", status="WARN" if offenders else "PASS",
            evidence={"wildcardBindings": len(offenders), "resources": offenders[:50]}, recommendation="Substituir wildcards pelos verbos e resources estritamente necessários."))
    else:
        controls.append(unavailable("cis.k8s.rbac.wildcards", "RBAC sem permissões wildcard", "KubernetesAPI", "Roles ou ClusterRoles indisponíveis"))

    if coverage_available(collection, "clusterrolebindings"):
        offenders = []
        for binding in items(raw.get("clusterrolebindings")):
            ref = binding.get("roleRef") or {}
            if ref.get("name") == "cluster-admin": offenders.append((binding.get("metadata") or {}).get("name", "unknown"))
        controls.append(control("cis.k8s.rbac.cluster-admin", "Uso restrito de cluster-admin", status="WARN" if offenders else "PASS",
            evidence={"bindings": offenders}, recommendation="Remover bindings amplos e adotar roles de menor privilégio."))
    else:
        controls.append(unavailable("cis.k8s.rbac.cluster-admin", "Uso restrito de cluster-admin", "KubernetesAPI", "ClusterRoleBindings indisponíveis"))

    def workload_check(control_id: str, title: str, predicate, recommendation: str) -> None:
        offenders = [f"{ns}/{kind}/{name}" for ns, name, kind, spec in specs if predicate(spec)]
        controls.append(control(control_id, title, status="WARN" if offenders else ("PASS" if specs else "UNKNOWN"),
            applicability="APPLICABLE" if specs else "EVIDENCE_UNAVAILABLE", evidence={"workloadsEvaluated": len(specs), "resources": offenders[:100]}, recommendation=recommendation))

    containers = lambda spec: [*(spec.get("initContainers") or []), *(spec.get("containers") or []), *(spec.get("ephemeralContainers") or [])]
    workload_check("cis.k8s.pod.privileged", "Containers não privilegiados", lambda s: any((c.get("securityContext") or {}).get("privileged") is True for c in containers(s)), "Remover privileged e conceder somente capacidades indispensáveis.")
    workload_check("cis.k8s.pod.host-namespaces", "Isolamento de namespaces do host", lambda s: any(s.get(key) is True for key in ("hostNetwork", "hostPID", "hostIPC")), "Desabilitar hostNetwork, hostPID e hostIPC salvo exceção formalmente aprovada.")
    workload_check("cis.k8s.pod.run-as-non-root", "Execução como usuário não root", lambda s: (s.get("securityContext") or {}).get("runAsNonRoot") is not True and any((c.get("securityContext") or {}).get("runAsNonRoot") is not True for c in containers(s)), "Definir runAsNonRoot=true no Pod ou em todos os containers.")
    workload_check("cis.k8s.pod.seccomp", "Perfil seccomp explícito", lambda s: not (s.get("securityContext") or {}).get("seccompProfile") and any(not (c.get("securityContext") or {}).get("seccompProfile") for c in containers(s)), "Definir seccompProfile.type como RuntimeDefault ou Localhost aprovado.")
    workload_check("cis.k8s.service-account.default", "Uso explícito de ServiceAccount", lambda s: (s.get("serviceAccountName") or "default") == "default" and s.get("automountServiceAccountToken") is not False, "Criar ServiceAccount dedicado e desabilitar automount do token quando não necessário.")
    workload_check("cis.k8s.pod.capabilities", "Capabilities Linux restritas", lambda s: any(set(((c.get("securityContext") or {}).get("capabilities") or {}).get("add") or []) - {"NET_BIND_SERVICE"} for c in containers(s)), "Remover capabilities adicionadas; manter apenas NET_BIND_SERVICE quando formalmente necessário.")
    workload_check("cis.k8s.pod.privilege-escalation", "Privilege escalation desabilitada", lambda s: any((c.get("securityContext") or {}).get("allowPrivilegeEscalation") is not False for c in containers(s)), "Definir allowPrivilegeEscalation=false em todos os containers.")
    workload_check("cis.k8s.pod.read-only-root-filesystem", "Root filesystem somente leitura", lambda s: any((c.get("securityContext") or {}).get("readOnlyRootFilesystem") is not True for c in containers(s)), "Definir readOnlyRootFilesystem=true e usar volumes graváveis somente onde necessário.")
    workload_check("cis.k8s.image.latest-tag", "Imagens sem tag latest ou tag implícita", lambda s: any((lambda image: "@sha256:" not in image and (":" not in image.rsplit("/", 1)[-1] or image.endswith(":latest")))(str(c.get("image") or "")) for c in containers(s)), "Usar versão imutável e evitar latest ou tag implícita.")
    workload_check("cis.k8s.image.digest", "Imagens fixadas por digest", lambda s: any("@sha256:" not in str(c.get("image") or "") for c in containers(s)), "Fixar imagens aprovadas por digest sha256 e manter processo de atualização controlado.")
    workload_check("cis.k8s.pod.host-path", "Volumes hostPath restritos", lambda s: any("hostPath" in (volume or {}) for volume in s.get("volumes") or []), "Substituir hostPath por storage gerenciado ou documentar exceção de infraestrutura.")
    workload_check("cis.k8s.pod.proc-mount", "procMount padrão", lambda s: any((c.get("securityContext") or {}).get("procMount") not in {None, "Default"} for c in containers(s)), "Usar procMount=Default.")
    unsafe_sysctls = {"kernel.shm_rmid_forced", "net.ipv4.ip_local_port_range", "net.ipv4.ip_unprivileged_port_start", "net.ipv4.tcp_syncookies", "net.ipv4.ping_group_range"}
    workload_check("cis.k8s.pod.sysctls", "Sysctls restritos", lambda s: any(str(x.get("name")) not in unsafe_sysctls for x in (s.get("securityContext") or {}).get("sysctls") or []), "Remover sysctls inseguros ou aplicar allowlist formal.")

    if coverage_available(collection, "services"):
        exposed = []
        for service in items(raw.get("services")):
            if (service.get("spec") or {}).get("type") in {"NodePort", "LoadBalancer"}:
                meta = service.get("metadata") or {}; exposed.append(f"{meta.get('namespace','default')}/Service/{meta.get('name','unknown')}")
        controls.append(control("cis.k8s.network.external-services", "Services externos revisados", status="WARN" if exposed else "PASS", evidence={"externalServices": exposed}, recommendation="Confirmar necessidade, controles de entrada, TLS, autenticação e restrição de origem para cada Service externo."))
    else:
        controls.append(unavailable("cis.k8s.network.external-services", "Services externos revisados", "KubernetesAPI", "Services indisponíveis"))

    if all(coverage_available(collection, key) for key in ("validatingwebhooks", "mutatingwebhooks", "kyverno_clusterpolicies")):
        mechanisms = sum(len(items(raw.get(key))) for key in ("validatingwebhooks", "mutatingwebhooks", "kyverno_clusterpolicies"))
        controls.append(control("cis.k8s.admission.policy-enforcement", "Políticas de admission configuradas", status="PASS" if mechanisms else "WARN", evidence={"mechanisms": mechanisms}, recommendation="Aplicar políticas de admission para requisitos de segurança de Pods e imagens."))
    else:
        controls.append(unavailable("cis.k8s.admission.policy-enforcement", "Políticas de admission configuradas", "KubernetesAPI", "Cobertura de admission webhooks/policies incompleta"))
    if coverage_available(collection, "validatingwebhooks") and coverage_available(collection, "mutatingwebhooks"):
        unsafe = []
        for config in items(raw.get("validatingwebhooks")) + items(raw.get("mutatingwebhooks")):
            meta = config.get("metadata") or {}
            if any(webhook.get("failurePolicy", "Fail") == "Ignore" for webhook in config.get("webhooks") or []): unsafe.append(str(meta.get("name") or "unknown"))
        controls.append(control("cis.k8s.admission.failure-policy", "Admission webhooks fail closed", status="WARN" if unsafe else "PASS", evidence={"failurePolicyIgnore": unsafe}, recommendation="Usar failurePolicy=Fail para políticas de segurança críticas e testar disponibilidade."))
    else:
        controls.append(unavailable("cis.k8s.admission.failure-policy", "Admission webhooks fail closed", "KubernetesAPI", "Admission webhooks indisponíveis"))

    namespaces = items(base.get("namespaces"))
    if namespaces:
        system_namespaces = {"kube-system", "kube-public", "kube-node-lease"}
        missing_psa = sorted(str((ns.get("metadata") or {}).get("name")) for ns in namespaces if (ns.get("metadata") or {}).get("name") not in system_namespaces and not any(key.startswith("pod-security.kubernetes.io/") for key in ((ns.get("metadata") or {}).get("labels") or {})))
        controls.append(control("cis.k8s.admission.pod-security", "Pod Security Admission configurado", status="WARN" if missing_psa else "PASS", evidence={"namespacesWithoutPsaLabels": missing_psa}, recommendation="Aplicar labels enforce, audit e warn com versão fixada nos namespaces de aplicação."))
    else:
        controls.append(unavailable("cis.k8s.admission.pod-security", "Pod Security Admission configurado", "KubernetesAPI", "Namespaces indisponíveis"))

    if coverage_available(collection, "roles") and coverage_available(collection, "clusterroles"):
        secret_roles, impersonation_roles = [], []
        for role in items(raw.get("roles")) + items(raw.get("clusterroles")):
            meta = role.get("metadata") or {}; ref = f"{role.get('kind','Role')}/{meta.get('namespace','-')}/{meta.get('name','unknown')}"
            rules = role.get("rules") or []
            if any("secrets" in (rule.get("resources") or []) and set(rule.get("verbs") or []) & {"get", "list", "watch", "*"} for rule in rules): secret_roles.append(ref)
            if any("impersonate" in (rule.get("verbs") or []) or "*" in (rule.get("verbs") or []) and set(rule.get("resources") or []) & {"users", "groups", "serviceaccounts"} for rule in rules): impersonation_roles.append(ref)
        controls.append(control("cis.k8s.rbac.secrets", "Leitura de Secrets restrita", status="WARN" if secret_roles else "PASS", evidence={"roles": secret_roles[:100]}, recommendation="Conceder leitura de Secrets somente a identidades e namespaces indispensáveis."))
        controls.append(control("cis.k8s.rbac.impersonation", "Impersonation restrita", status="WARN" if impersonation_roles else "PASS", evidence={"roles": impersonation_roles[:100]}, recommendation="Remover impersonate e wildcards de identidades não administrativas."))
    else:
        controls.append(unavailable("cis.k8s.rbac.secrets", "Leitura de Secrets restrita", "KubernetesAPI", "Roles ou ClusterRoles indisponíveis"))
        controls.append(unavailable("cis.k8s.rbac.impersonation", "Impersonation restrita", "KubernetesAPI", "Roles ou ClusterRoles indisponíveis"))

    if coverage_available(collection, "networkpolicies"):
        workload_namespaces = {ns for ns, _name, _kind, _spec in specs}
        protected = {(p.get("metadata") or {}).get("namespace", "default") for p in items(raw.get("networkpolicies"))}
        missing = sorted(workload_namespaces - protected)
        controls.append(control("cis.k8s.network.network-policy", "NetworkPolicy para namespaces com workloads", status="WARN" if missing else ("PASS" if workload_namespaces else "UNKNOWN"), evidence={"namespacesWithoutPolicy": missing, "namespacesEvaluated": len(workload_namespaces)}, recommendation="Aplicar default-deny e liberar somente os fluxos necessários."))
    else:
        controls.append(unavailable("cis.k8s.network.network-policy", "NetworkPolicy para namespaces com workloads", "KubernetesAPI", "NetworkPolicies indisponíveis"))

    metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8")) if (directory / "metadata.json").is_file() else {}
    identity = f"{metadata.get('context','')} {metadata.get('clusterName','')}".lower()
    provider = "AWS" if (aws_eks or {}).get("state") == "AVAILABLE" or "eks" in identity or "arn:aws" in identity else "AZURE" if "aks" in identity or "azure" in identity else "GOOGLE" if "gke" in identity or "google" in identity else "SELF_MANAGED" if any("control-plane" in str((n.get("metadata") or {}).get("labels", {})) or "master" in str((n.get("metadata") or {}).get("labels", {})) for n in items(base.get("nodes"))) else "GENERIC"
    for control_id, title in (("cis.k8s.control-plane.api-server", "Configuração segura do kube-apiserver"), ("cis.k8s.control-plane.etcd", "Configuração segura do etcd")):
        if provider in {"AWS", "AZURE", "GOOGLE"}:
            controls.append(control(control_id, title, applicability="MANAGED_PROVIDER", mode="MANUAL", status="N/A", responsibility="CLOUD_PROVIDER", source="CloudProviderAPI", evidence={"provider": provider, "reason": "Control plane gerenciado pelo cloud provider"}, recommendation="Validar responsabilidades compartilhadas e evidências disponibilizadas pelo provider."))
        elif provider == "SELF_MANAGED":
            controls.append(unavailable(control_id, title, "ControlPlaneEvidence", "Cluster self-managed detectado, mas flags/configuração do control plane não foram fornecidas"))
        else:
            controls.append(control(control_id, title, applicability="MANUAL_REVIEW", mode="MANUAL", status="UNKNOWN", responsibility="SHARED", source="ManualEvidence", evidence={"reason": "Tipo de gerenciamento do control plane não comprovado"}, recommendation="Documentar o responsável pelo control plane e anexar evidência de configuração."))

    controls.append(control(
        "cis.k8s.node.kubelet-configuration", "Configuração segura do kubelet",
        applicability="EVIDENCE_UNAVAILABLE", status="UNKNOWN",
        responsibility="SHARED" if provider in {"AWS", "AZURE", "GOOGLE"} else "CUSTOMER",
        source="NodeEvidence", evidence={"reason": "Node evidence opcional não fornecida; nenhum acesso SSH ou filesystem foi tentado"},
        recommendation="Fornecer evidência sanitizada e autorizada da configuração do kubelet ou registrar revisão manual.",
    ))

    controls, evidence_summary = apply_external_and_lifecycle(controls, directory, aws_eks, provider)
    controls = [enrich_control(item) for item in controls]
    return {
        "schemaVersion": "1.1", "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(), "readOnly": True,
        "notice": "Avaliação de postura baseada no CIS. Não representa certificação nem compliance integral.",
        "benchmarkReference": {"family": "CIS Kubernetes Benchmarks", "genericVersion": "2.0.1", "providerVersion": "2.0.0", "url": CIS_REFERENCE},
        "platform": provider, "summary": {**summarize(controls), **evidence_summary},
        "controls": controls,
    }


def generate(directory: Path, raw: dict[str, Any], base: dict[str, Any], collection: dict[str, Any], aws_eks: dict[str, Any] | None = None) -> dict[str, Any]:
    report = assess(raw, base, collection, directory, aws_eks)
    (directory / "cis-security-assessment.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
