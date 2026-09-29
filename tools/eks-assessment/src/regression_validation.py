#!/usr/bin/env python3
"""Offline, provider-neutral regression gates for two sanitized collections."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Any

from cis_security_assessment import compare_reports
from provider_validation import artifact_validation


DEFAULT_POLICY = Path(__file__).resolve().parents[1] / "data" / "assessment-policy.json"
SEVERITY_RANK = {"N/A": 0, "PASS": 0, "INFO": 1, "UNKNOWN": 2, "PARTIAL": 2, "WARN": 3, "CRIT": 4}
NODE_STATE_RANK = {"PASS": 0, "PARTIAL": 1, "EVIDENCE_UNAVAILABLE": 1, "WARN": 2, "CRIT": 3}
INPUT_ARTIFACTS = (
    "metadata.json", "nodes.json", "pods.json", "workloads.json",
    "comprehensive-assessment.json", "application-manifests-sanitized.json",
    "api-resources.json", "universal-inventory.json", "aws-eks-assessment.json",
    "cloud-provider-assessment.json", "cis-security-assessment.json", "operational-insights.json",
)
BOOLEAN_KEYS = {
    "requireSameCluster", "requireCompletedCollections", "requireArtifactIntegrity",
    "requireCisEvidence", "requireOperationalEvidence",
}
LIMIT_KEYS = {
    "maxNewCritical", "maxNewWarnings", "maxSeverityRegressions", "maxEvidenceLoss",
    "maxCisRegressions", "maxCisEvidenceLoss", "maxCisPostureDropPercent",
    "maxCisCoverageDropPercent", "maxNodeStateRegressions", "maxNodeCriticalIncrease",
    "maxNodeWarningIncrease", "maxNodeMetricsCoverageDropPercent",
    "maxManifestCriticalIncrease", "maxManifestWarningIncrease", "maxEndOfSupportIncrease",
    "maxQualityRegression", "maxDurationIncreasePercent", "maxApiRequestsIncreasePercent",
    "maxResponseBytesIncreasePercent", "maxPeakRssIncreasePercent", "maxCurrentCritical",
    "maxCurrentWarnings", "maxCurrentUnknown", "maxCurrentPartial",
}


def load(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def integer(value: Any) -> int:
    number = finite_number(value)
    return int(number) if number is not None else 0


def nested(value: Any, *keys: str) -> Any:
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def policy_document(path: Path = DEFAULT_POLICY) -> dict[str, Any]:
    value = load(path, None)
    if not isinstance(value, dict):
        raise ValueError("policy ausente ou inválida")
    if value.get("schemaVersion") != "1.0":
        raise ValueError("schemaVersion da policy não suportado")
    profiles = value.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        raise ValueError("policy não contém profiles")
    if value.get("defaultProfile") not in profiles:
        raise ValueError("defaultProfile não existe na policy")
    return value


def profile_names(path: Path = DEFAULT_POLICY) -> tuple[str, ...]:
    names = tuple(sorted(policy_document(path)["profiles"]))
    for name in names:
        load_policy(path, name)
    return names


def load_policy(path: Path = DEFAULT_POLICY, profile: str | None = None) -> dict[str, Any]:
    document = policy_document(path)
    selected = profile or str(document["defaultProfile"])
    raw = document["profiles"].get(selected)
    if not isinstance(raw, dict):
        raise ValueError("profile de policy inexistente")
    unknown = set(raw).difference({"description"} | BOOLEAN_KEYS | LIMIT_KEYS)
    missing = (BOOLEAN_KEYS | LIMIT_KEYS).difference(raw)
    if unknown:
        raise ValueError("campos desconhecidos na policy: " + ", ".join(sorted(unknown)))
    if missing:
        raise ValueError("campos obrigatórios ausentes na policy: " + ", ".join(sorted(missing)))
    for key in BOOLEAN_KEYS:
        if not isinstance(raw.get(key), bool):
            raise ValueError(f"{key} deve ser boolean")
        if raw.get(key) is not True:
            raise ValueError(f"{key} não pode ser desabilitado")
    for key in LIMIT_KEYS:
        value = raw.get(key)
        if value is None and key.startswith("maxCurrent"):
            continue
        if finite_number(value) is None:
            raise ValueError(f"{key} deve ser número não negativo")
    return {
        "schemaVersion": str(document["schemaVersion"]),
        "profile": selected,
        "description": str(raw.get("description") or ""),
        "source": path.name,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "thresholds": {key: raw[key] for key in sorted(BOOLEAN_KEYS | LIMIT_KEYS)},
    }


def gate(gate_id: str, category: str, status: str, summary: str,
         evidence: dict[str, Any], *, mandatory: bool = True) -> dict[str, Any]:
    return {
        "gateId": gate_id,
        "category": category,
        "status": status,
        "mandatory": mandatory,
        "summary": summary,
        "evidence": evidence,
    }


def collection(path: Path) -> dict[str, Any]:
    return {
        "metadata": load(path / "metadata.json", {}),
        "comprehensive": load(path / "comprehensive-assessment.json", {}),
        "cis": load(path / "cis-security-assessment.json", {}),
        "operational": load(path / "operational-insights.json", {}),
    }


def collection_digest(path: Path) -> str:
    digest = hashlib.sha256()
    for filename in INPUT_ARTIFACTS:
        artifact = path / filename
        digest.update(filename.encode("utf-8") + b"\0")
        try:
            digest.update(artifact.read_bytes())
        except OSError:
            digest.update(b"MISSING")
        digest.update(b"\0")
    return digest.hexdigest()


def completed(value: dict[str, Any]) -> bool:
    metadata = value.get("metadata") or {}
    return metadata.get("completed") is True and str(metadata.get("status") or "").upper() == "COMPLETED"


def scope_evidence(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    old, new = before.get("metadata") or {}, after.get("metadata") or {}
    signals: dict[str, str] = {}
    for key in ("clusterName", "context"):
        old_value, new_value = str(old.get(key) or "").strip(), str(new.get(key) or "").strip()
        if old_value and new_value:
            signals[key] = "MATCH" if old_value == new_value else "MISMATCH"
    old_namespace = str(
        old.get("namespaceScope")
        or nested(before, "comprehensive", "collection", "namespaceScope")
        or ""
    ).strip()
    new_namespace = str(
        new.get("namespaceScope")
        or nested(after, "comprehensive", "collection", "namespaceScope")
        or ""
    ).strip()
    normalized_old = "*" if old_namespace in {"*", "all"} else old_namespace
    normalized_new = "*" if new_namespace in {"*", "all"} else new_namespace
    namespace_available = bool(normalized_old and normalized_new)
    signals["namespaceScope"] = (
        "MATCH" if namespace_available and normalized_old == normalized_new
        else "MISMATCH" if namespace_available
        else "UNAVAILABLE"
    )
    cluster_signals = [signals[key] for key in ("clusterName", "context") if key in signals]
    same_cluster = bool(cluster_signals) and all(value == "MATCH" for value in cluster_signals)
    same_scope = same_cluster and signals["namespaceScope"] == "MATCH"
    return {
        "signals": signals,
        "available": bool(cluster_signals) and namespace_available,
        "sameCluster": same_cluster,
        "sameNamespaceScope": signals["namespaceScope"] == "MATCH",
        "sameCollectionScope": same_scope,
    }


def finding_identity(item: dict[str, Any]) -> str:
    return str(
        item.get("fingerprint")
        or (f'{item.get("ruleId")}|{item.get("resourceKey")}' if item.get("ruleId") and item.get("resourceKey") else "")
        or item.get("id")
        or ""
    )


def safe_change(kind: str, item: dict[str, Any], before_status: str | None = None) -> dict[str, Any]:
    detail = str(item.get("detail") or item.get("evidence") or "")
    if len(detail) > 500:
        detail = detail[:497] + "..."
    return {
        "change": kind,
        "fingerprint": finding_identity(item),
        "ruleId": str(item.get("ruleId") or "UNKNOWN"),
        "category": str(item.get("category") or "UNKNOWN"),
        "namespace": str(item.get("namespace") or "-"),
        "resource": str(item.get("workload") or item.get("resourceKey") or "-"),
        "check": str(item.get("check") or "-"),
        "beforeStatus": before_status or "ABSENT",
        "afterStatus": str(item.get("severity") or "UNKNOWN"),
        "detail": detail,
    }


def findings_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    old_items = [item for item in nested(before, "comprehensive", "findings") or [] if isinstance(item, dict)]
    new_items = [item for item in nested(after, "comprehensive", "findings") or [] if isinstance(item, dict)]
    old_map = {finding_identity(item): item for item in old_items if finding_identity(item)}
    new_map = {finding_identity(item): item for item in new_items if finding_identity(item)}
    new_risks, regressions, evidence_loss, resolved = [], [], [], []
    for key, item in new_map.items():
        severity = str(item.get("severity") or "UNKNOWN")
        previous = old_map.get(key)
        if previous is None:
            if severity in {"CRIT", "WARN"}:
                new_risks.append(safe_change("NEW_RISK", item))
            elif severity in {"UNKNOWN", "PARTIAL"}:
                evidence_loss.append(safe_change("EVIDENCE_LOSS", item))
            continue
        old_severity = str(previous.get("severity") or "UNKNOWN")
        if SEVERITY_RANK.get(severity, 2) > SEVERITY_RANK.get(old_severity, 2):
            regressions.append(safe_change("SEVERITY_REGRESSION", item, old_severity))
        if severity in {"UNKNOWN", "PARTIAL"} and old_severity not in {"UNKNOWN", "PARTIAL"}:
            evidence_loss.append(safe_change("EVIDENCE_LOSS", item, old_severity))
    for key, item in old_map.items():
        severity = str(item.get("severity") or "UNKNOWN")
        current = new_map.get(key)
        if severity in {"CRIT", "WARN"} and (
            current is None or SEVERITY_RANK.get(str(current.get("severity") or "UNKNOWN"), 2) < SEVERITY_RANK[severity]
        ):
            change = safe_change("RESOLVED", current or item, severity)
            if current is None:
                change["afterStatus"] = "ABSENT"
            resolved.append(change)
    current_counts = Counter(str(item.get("severity") or "UNKNOWN") for item in new_items)
    return {
        "newRisks": new_risks,
        "severityRegressions": regressions,
        "evidenceLoss": evidence_loss,
        "resolvedRisks": resolved,
        "currentStatus": dict(current_counts),
    }


def finding_gates(delta: dict[str, Any], policy: dict[str, Any]) -> list[dict[str, Any]]:
    thresholds = policy["thresholds"]
    new_critical = sum(item["afterStatus"] == "CRIT" for item in delta["newRisks"])
    new_warnings = sum(item["afterStatus"] == "WARN" for item in delta["newRisks"])
    new_failed = new_critical > thresholds["maxNewCritical"] or new_warnings > thresholds["maxNewWarnings"]
    regression_count = len(delta["severityRegressions"])
    evidence_count = len(delta["evidenceLoss"])
    current = delta["currentStatus"]
    absolute_limits = {
        "CRIT": thresholds["maxCurrentCritical"],
        "WARN": thresholds["maxCurrentWarnings"],
        "UNKNOWN": thresholds["maxCurrentUnknown"],
        "PARTIAL": thresholds["maxCurrentPartial"],
    }
    absolute_exceeded = {
        state: {"value": integer(current.get(state)), "limit": limit}
        for state, limit in absolute_limits.items()
        if limit is not None and integer(current.get(state)) > limit
    }
    return [
        gate(
            "findings.new-risk", "Findings", "FAIL" if new_failed else "PASS",
            "Novos riscos excedem a policy." if new_failed else "Nenhum novo risco excede a policy.",
            {"newCritical": new_critical, "newWarnings": new_warnings,
             "limits": {"newCritical": thresholds["maxNewCritical"], "newWarnings": thresholds["maxNewWarnings"]}},
        ),
        gate(
            "findings.severity-regression", "Findings",
            "FAIL" if regression_count > thresholds["maxSeverityRegressions"] else "PASS",
            "Severidades regrediram além do limite." if regression_count > thresholds["maxSeverityRegressions"] else "Não houve regressão de severidade acima do limite.",
            {"regressions": regression_count, "limit": thresholds["maxSeverityRegressions"]},
        ),
        gate(
            "evidence.loss", "Evidence",
            "FAIL" if evidence_count > thresholds["maxEvidenceLoss"] else "PASS",
            "A coleta perdeu evidência além do limite." if evidence_count > thresholds["maxEvidenceLoss"] else "Não houve perda de evidência acima do limite.",
            {"evidenceLoss": evidence_count, "limit": thresholds["maxEvidenceLoss"]},
        ),
        gate(
            "findings.current-risk", "Findings", "FAIL" if absolute_exceeded else "PASS",
            "A coleta atual excede os limites absolutos do profile." if absolute_exceeded else "Os limites absolutos do profile foram atendidos ou não se aplicam.",
            {"current": {key: integer(current.get(key)) for key in ("CRIT", "WARN", "UNKNOWN", "PARTIAL")},
             "limits": absolute_limits, "exceeded": absolute_exceeded},
        ),
    ]


def cis_gate(before: dict[str, Any], after: dict[str, Any], policy: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    old, new = before.get("cis") or {}, after.get("cis") or {}
    required = policy["thresholds"]["requireCisEvidence"]
    if not old.get("controls") or not new.get("controls"):
        return gate(
            "cis.regression", "CIS", "FAIL" if required else "WARN",
            "Evidência CIS insuficiente para comparação.", {"available": False}, mandatory=required,
        ), {}
    delta = compare_reports(old, new)
    counts = delta.get("counts") or {}
    thresholds = policy["thresholds"]
    posture_drop = max(0.0, -float(delta.get("postureDelta") or 0))
    coverage_drop = max(0.0, -float(delta.get("coverageDelta") or 0))
    values = {
        "regressions": integer(counts.get("REGRESSION")),
        "evidenceLoss": integer(counts.get("EVIDENCE_LOSS")),
        "postureDropPercent": posture_drop,
        "coverageDropPercent": coverage_drop,
    }
    limits = {
        "regressions": thresholds["maxCisRegressions"],
        "evidenceLoss": thresholds["maxCisEvidenceLoss"],
        "postureDropPercent": thresholds["maxCisPostureDropPercent"],
        "coverageDropPercent": thresholds["maxCisCoverageDropPercent"],
    }
    exceeded = sorted(key for key in values if values[key] > limits[key])
    result = gate(
        "cis.regression", "CIS", "FAIL" if exceeded else "PASS",
        "A postura CIS regrediu além da policy." if exceeded else "A postura CIS não regrediu além da policy.",
        {"values": values, "limits": limits, "exceeded": exceeded},
    )
    return result, delta


def node_gate(before: dict[str, Any], after: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    old = nested(before, "operational", "nodeHealth") or {}
    new = nested(after, "operational", "nodeHealth") or {}
    required = policy["thresholds"]["requireOperationalEvidence"]
    old_summary, new_summary = old.get("summary") or {}, new.get("summary") or {}
    if not old_summary or not new_summary:
        return gate("node-health.regression", "Node Health", "FAIL" if required else "WARN",
                    "Node Health indisponível para comparação.", {"available": False}, mandatory=required)
    old_items = {str(item.get("node")): item for item in old.get("items") or [] if isinstance(item, dict)}
    new_items = {str(item.get("node")): item for item in new.get("items") or [] if isinstance(item, dict)}
    state_regressions = sum(
        NODE_STATE_RANK.get(str(item.get("state")), 1) > NODE_STATE_RANK.get(str(old_items[name].get("state")), 1)
        for name, item in new_items.items() if name in old_items
    )
    old_coverage, new_coverage = finite_number(old_summary.get("metricsCoveragePercent")), finite_number(new_summary.get("metricsCoveragePercent"))
    coverage_drop = max(0.0, (old_coverage or 0.0) - (new_coverage or 0.0)) if old_coverage is not None and new_coverage is not None else None
    values = {
        "stateRegressions": state_regressions,
        "criticalIncrease": max(0, integer(new_summary.get("critical")) - integer(old_summary.get("critical"))),
        "warningIncrease": max(0, integer(new_summary.get("warnings")) - integer(old_summary.get("warnings"))),
        "metricsCoverageDropPercent": coverage_drop,
    }
    limits = {
        "stateRegressions": policy["thresholds"]["maxNodeStateRegressions"],
        "criticalIncrease": policy["thresholds"]["maxNodeCriticalIncrease"],
        "warningIncrease": policy["thresholds"]["maxNodeWarningIncrease"],
        "metricsCoverageDropPercent": policy["thresholds"]["maxNodeMetricsCoverageDropPercent"],
    }
    missing = [key for key, value in values.items() if value is None]
    exceeded = sorted(key for key, value in values.items() if value is not None and value > limits[key])
    status = "FAIL" if exceeded else "WARN" if missing else "PASS"
    return gate(
        "node-health.regression", "Node Health", status,
        "Node Health regrediu além da policy." if exceeded else "Node Health possui evidência incompleta." if missing else "Node Health não regrediu além da policy.",
        {"values": values, "limits": limits, "missing": missing, "exceeded": exceeded},
    )


def operational_gate(before: dict[str, Any], after: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    old_manifest = nested(before, "operational", "manifestQuality", "summary") or {}
    new_manifest = nested(after, "operational", "manifestQuality", "summary") or {}
    old_versions = nested(before, "operational", "versions", "summary") or {}
    new_versions = nested(after, "operational", "versions", "summary") or {}
    required = policy["thresholds"]["requireOperationalEvidence"]
    if not old_manifest or not new_manifest or not old_versions or not new_versions:
        return gate(
            "operational.regression", "Operational Insights", "FAIL" if required else "WARN",
            "Manifest Quality ou Versions & Lifecycle indisponível para comparação.",
            {"manifestAvailable": bool(old_manifest and new_manifest), "versionsAvailable": bool(old_versions and new_versions)},
            mandatory=required,
        )
    values = {
        "manifestCriticalIncrease": max(0, integer(new_manifest.get("critical")) - integer(old_manifest.get("critical"))),
        "manifestWarningIncrease": max(0, integer(new_manifest.get("warnings")) - integer(old_manifest.get("warnings"))),
        "endOfSupportIncrease": max(0, integer(new_versions.get("endOfSupport")) - integer(old_versions.get("endOfSupport"))),
        "nodeVersionSkewIntroduced": 1 if new_versions.get("nodeVersionSkew") is True and old_versions.get("nodeVersionSkew") is not True else 0,
    }
    limits = {
        "manifestCriticalIncrease": policy["thresholds"]["maxManifestCriticalIncrease"],
        "manifestWarningIncrease": policy["thresholds"]["maxManifestWarningIncrease"],
        "endOfSupportIncrease": policy["thresholds"]["maxEndOfSupportIncrease"],
        "nodeVersionSkewIntroduced": 0,
    }
    exceeded = sorted(key for key in values if values[key] > limits[key])
    return gate(
        "operational.regression", "Operational Insights", "FAIL" if exceeded else "PASS",
        "Operational Insights regrediu além da policy." if exceeded else "Manifest Quality e lifecycle não regrediram além da policy.",
        {"values": values, "limits": limits, "exceeded": exceeded},
    )


def quality_gate(before: dict[str, Any], after: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    old = nested(before, "comprehensive", "quality") or {}
    new = nested(after, "comprehensive", "quality") or {}
    fields = ("stableIdentityDuplicates", "conflictingSeverities", "lowConfidencePasses")
    if not old or not new:
        return gate("quality.regression", "Quality", "WARN", "Quality gate indisponível para comparação.", {"available": False})
    increases = {key: max(0, integer(new.get(key)) - integer(old.get(key))) for key in fields}
    total = sum(increases.values())
    limit = policy["thresholds"]["maxQualityRegression"]
    return gate(
        "quality.regression", "Quality", "FAIL" if total > limit else "PASS",
        "A qualidade dos findings regrediu." if total > limit else "A qualidade dos findings não regrediu.",
        {"increases": increases, "total": total, "limit": limit},
    )


def performance_values(value: dict[str, Any]) -> dict[str, float | None]:
    budget = nested(value, "comprehensive", "performance", "requestBudget") or {}
    return {
        "duration": finite_number(nested(value, "metadata", "performance", "durationSeconds")),
        "apiRequests": finite_number(budget.get("requests")),
        "responseBytes": finite_number(budget.get("responseBytes")),
        "peakRss": finite_number(nested(value, "comprehensive", "performance", "processPeakRssBytes")),
    }


def increase_percent(before: float | None, after: float | None) -> float | None:
    if before is None or after is None:
        return None
    if before == 0:
        return 0.0 if after == 0 else None
    return round((after - before) * 100.0 / before, 2)


def performance_gate(before: dict[str, Any], after: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    old, new = performance_values(before), performance_values(after)
    increases = {key: increase_percent(old[key], new[key]) for key in old}
    limits = {
        "duration": policy["thresholds"]["maxDurationIncreasePercent"],
        "apiRequests": policy["thresholds"]["maxApiRequestsIncreasePercent"],
        "responseBytes": policy["thresholds"]["maxResponseBytesIncreasePercent"],
        "peakRss": policy["thresholds"]["maxPeakRssIncreasePercent"],
    }
    missing = sorted(key for key, value in increases.items() if value is None)
    exceeded = sorted(key for key, value in increases.items() if value is not None and value > limits[key])
    status = "FAIL" if exceeded else "WARN" if missing else "PASS"
    return gate(
        "performance.regression", "Performance", status,
        "O impacto da coleta regrediu além da policy." if exceeded else "Métricas de performance insuficientes." if missing else "O impacto da coleta permaneceu dentro da policy.",
        {"before": old, "after": new, "increasePercent": increases, "limitsPercent": limits,
         "missing": missing, "exceeded": exceeded},
    )


def scope_blocked_gates() -> list[dict[str, Any]]:
    reason = {"reason": "COLLECTION_SCOPE_MISMATCH"}
    definitions = (
        ("findings.new-risk", "Findings"),
        ("findings.severity-regression", "Findings"),
        ("evidence.loss", "Evidence"),
        ("findings.current-risk", "Findings"),
        ("cis.regression", "CIS"),
        ("node-health.regression", "Node Health"),
        ("operational.regression", "Operational"),
        ("quality.regression", "Quality"),
        ("performance.regression", "Performance"),
    )
    return [
        gate(
            gate_id,
            category,
            "N/A",
            "Não avaliado porque cluster ou namespace scope divergem.",
            reason,
            mandatory=False,
        )
        for gate_id, category in definitions
    ]


def evaluate(before_path: Path, after_path: Path, *, policy_path: Path = DEFAULT_POLICY,
             profile: str | None = None, validate_artifacts: bool = True) -> dict[str, Any]:
    before_path, after_path = before_path.resolve(), after_path.resolve()
    if not before_path.is_dir() or not after_path.is_dir():
        raise ValueError("diretório de coleta não encontrado")
    if before_path == after_path:
        raise ValueError("as coletas anterior e atual devem ser diferentes")
    selected_policy = load_policy(policy_path.resolve(), profile)
    thresholds = selected_policy["thresholds"]
    before, after = collection(before_path), collection(after_path)
    scope = scope_evidence(before, after)
    scope_status = "PASS" if scope["sameCollectionScope"] else "FAIL" if thresholds["requireSameCluster"] else "WARN"
    terminal_ok = completed(before) and completed(after)
    gates = [
        gate(
            "comparison.scope", "Comparison", scope_status,
            "As coletas pertencem ao mesmo cluster e namespace scope." if scope["sameCollectionScope"] else "Não foi possível comprovar o mesmo cluster e namespace scope nas duas coletas.",
            scope, mandatory=thresholds["requireSameCluster"],
        ),
        gate(
            "collections.terminal-state", "Collection",
            "PASS" if terminal_ok else "FAIL" if thresholds["requireCompletedCollections"] else "WARN",
            "As duas coletas estão concluídas." if terminal_ok else "Uma ou mais coletas não estão concluídas.",
            {"beforeCompleted": completed(before), "afterCompleted": completed(after)},
            mandatory=thresholds["requireCompletedCollections"],
        ),
    ]
    if validate_artifacts:
        old_artifacts, new_artifacts = artifact_validation(before_path), artifact_validation(after_path)
        integrity_ok = old_artifacts.get("ok") is True and new_artifacts.get("ok") is True
        gates.append(gate(
            "artifacts.integrity", "Artifacts",
            "PASS" if integrity_ok else "FAIL" if thresholds["requireArtifactIntegrity"] else "WARN",
            "Os artefatos das duas coletas foram validados." if integrity_ok else "A integridade de uma ou mais coletas não foi comprovada.",
            {"beforeOk": old_artifacts.get("ok") is True, "afterOk": new_artifacts.get("ok") is True,
             "beforeErrors": len(old_artifacts.get("errors") or []), "afterErrors": len(new_artifacts.get("errors") or [])},
            mandatory=thresholds["requireArtifactIntegrity"],
        ))
    if scope["sameCollectionScope"]:
        finding_delta = findings_delta(before, after)
        gates.extend(finding_gates(finding_delta, selected_policy))
        cis_result, cis_delta = cis_gate(before, after, selected_policy)
        gates.extend([
            cis_result,
            node_gate(before, after, selected_policy),
            operational_gate(before, after, selected_policy),
            quality_gate(before, after, selected_policy),
            performance_gate(before, after, selected_policy),
        ])
    else:
        finding_delta = {
            "newRisks": [],
            "severityRegressions": [],
            "evidenceLoss": [],
            "resolvedRisks": [],
            "currentStatus": {},
        }
        cis_delta = {}
        gates.extend(scope_blocked_gates())
    mandatory = [item for item in gates if item.get("mandatory")]
    release_ready = all(item.get("status") == "PASS" for item in mandatory)
    state = "FAIL" if any(item.get("status") == "FAIL" for item in mandatory) else "WARN" if any(item.get("status") == "WARN" for item in mandatory) else "PASS"
    counts = Counter(str(item.get("status")) for item in gates)
    changes = (
        finding_delta["newRisks"] + finding_delta["severityRegressions"]
        + finding_delta["evidenceLoss"] + finding_delta["resolvedRisks"]
    )
    cis_changes = [
        {key: item.get(key) for key in ("change", "controlId", "domain", "beforeStatus", "afterStatus", "beforeApplicability", "afterApplicability")}
        for item in (cis_delta.get("changes") or [])[:500]
    ]
    return {
        "schemaVersion": "1.0",
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "readOnly": True,
        "notice": "Regression Gate offline baseado em artefatos sanitizados. Não executa chamadas ao cluster e não representa certificação.",
        "comparison": {
            "before": before_path.name,
            "after": after_path.name,
            "beforeSha256": collection_digest(before_path),
            "afterSha256": collection_digest(after_path),
            **scope,
        },
        "policy": selected_policy,
        "summary": {
            "state": state, "releaseReady": release_ready, "gates": len(gates), "status": dict(counts),
            "newRisks": len(finding_delta["newRisks"]),
            "severityRegressions": len(finding_delta["severityRegressions"]),
            "evidenceLoss": len(finding_delta["evidenceLoss"]),
            "resolvedRisks": len(finding_delta["resolvedRisks"]),
            "cisRegressions": integer((cis_delta.get("counts") or {}).get("REGRESSION")),
        },
        "changes": changes[:500],
        "changesTruncated": len(changes) > 500,
        "cisChanges": cis_changes,
        "gates": gates,
    }


def junit_xml(report: dict[str, Any]) -> str:
    gates = report.get("gates") or []
    blocking = [item for item in gates if item.get("mandatory") and item.get("status") != "PASS"]
    suite = ET.Element("testsuite", {
        "name": "Kubernetes Assessment Regression Gate",
        "tests": str(len(gates)),
        "failures": str(len(blocking)),
        "errors": "0",
        "skipped": "0",
    })
    for item in gates:
        case = ET.SubElement(suite, "testcase", {
            "classname": str(item.get("category") or "Regression"),
            "name": str(item.get("gateId") or "unknown"),
        })
        if item.get("mandatory") and item.get("status") != "PASS":
            failure = ET.SubElement(case, "failure", {
                "type": str(item.get("status") or "UNKNOWN"),
                "message": str(item.get("summary") or "Gate bloqueado"),
            })
            failure.text = json.dumps(item.get("evidence") or {}, ensure_ascii=False, sort_keys=True)
    return ET.tostring(suite, encoding="unicode", xml_declaration=True) + "\n"


def sarif(report: dict[str, Any]) -> dict[str, Any]:
    rules, results = [], []
    for item in report.get("gates") or []:
        rule_id = str(item.get("gateId") or "regression.unknown")
        rules.append({"id": rule_id, "shortDescription": {"text": str(item.get("summary") or rule_id)}})
        if item.get("mandatory") and item.get("status") != "PASS":
            results.append({
                "ruleId": rule_id,
                "level": "error" if item.get("status") == "FAIL" else "warning",
                "message": {"text": str(item.get("summary") or "Regression Gate bloqueado")},
                "properties": {"status": item.get("status"), "evidence": item.get("evidence") or {}},
            })
    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [{"tool": {"driver": {"name": "Kubernetes Assessment Regression Gate", "rules": rules}}, "results": results}],
    }


def atomic_write(path: Path, value: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_text(value, encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def write_outputs(report: dict[str, Any], json_path: Path, junit_path: Path, sarif_path: Path) -> None:
    atomic_write(json_path, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    atomic_write(junit_path, junit_xml(report))
    atomic_write(sarif_path, json.dumps(sarif(report), ensure_ascii=False, indent=2) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Regression Gate offline para duas coletas sanitizadas")
    parser.add_argument("--before", required=True, type=Path)
    parser.add_argument("--after", required=True, type=Path)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--profile")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--junit-output", type=Path)
    parser.add_argument("--sarif-output", type=Path)
    args = parser.parse_args()
    before, after, policy_path = args.before.resolve(), args.after.resolve(), args.policy.resolve()
    if not before.is_dir() or not after.is_dir():
        parser.error("diretório de coleta não encontrado")
    try:
        report = evaluate(before, after, policy_path=policy_path, profile=args.profile)
    except ValueError as error:
        parser.error(str(error))
    output = args.output.resolve() if args.output else after / "regression-validation.json"
    junit_output = args.junit_output.resolve() if args.junit_output else after / "regression-validation.junit.xml"
    sarif_output = args.sarif_output.resolve() if args.sarif_output else after / "regression-validation.sarif.json"
    for path in (output, junit_output, sarif_output):
        if not path.parent.is_dir():
            parser.error(f"diretório de saída não encontrado: {path.parent}")
    write_outputs(report, output, junit_output, sarif_output)
    print(json.dumps({
        "state": report["summary"]["state"],
        "releaseReady": report["summary"]["releaseReady"],
        "profile": report["policy"]["profile"],
        "gates": report["summary"]["gates"],
        "newRisks": report["summary"]["newRisks"],
        "output": output.name,
    }, ensure_ascii=False))
    return 0 if report["summary"]["releaseReady"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
