#!/usr/bin/env python3
"""Offline release gates for sanitized EKS, AKS, GKE or Kubernetes collections."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cloud_provider_assessment import detected_platform


PROVIDERS = ("eks", "aks", "gke", "generic-kubernetes")
READ_ONLY_CLOUD_OPERATIONS = {"get", "list", "describe", "show"}
FORBIDDEN_CLOUD_KEYS = {
    "accountid", "subscriptionid", "tenantid", "projectid", "resourceid",
    "fqdn", "endpoint", "endpointurl",
}
CONTROL_PLANE_CONTROLS = {
    "cis.k8s.control-plane.api-server",
    "cis.k8s.control-plane.etcd",
}


@dataclass(frozen=True)
class Thresholds:
    max_duration_seconds: float = 1800.0
    max_api_requests: int = 1000
    max_cloud_api_requests: int = 100
    max_api_retries: int = 3
    max_api_throttles: int = 0
    max_response_bytes: int = 256 * 1024 * 1024
    max_peak_rss_bytes: int = 512 * 1024 * 1024
    min_kubernetes_coverage_percent: float = 80.0
    min_cloud_coverage_percent: float = 80.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "maxDurationSeconds": self.max_duration_seconds,
            "maxApiRequests": self.max_api_requests,
            "maxCloudApiRequests": self.max_cloud_api_requests,
            "maxApiRetries": self.max_api_retries,
            "maxApiThrottles": self.max_api_throttles,
            "maxResponseBytes": self.max_response_bytes,
            "maxPeakRssBytes": self.max_peak_rss_bytes,
            "minKubernetesCoveragePercent": self.min_kubernetes_coverage_percent,
            "minCloudCoveragePercent": self.min_cloud_coverage_percent,
        }


def load(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def nested(value: Any, *keys: str, fallback: Any = None) -> Any:
    for key in keys:
        if not isinstance(value, dict):
            return fallback
        value = value.get(key)
    return fallback if value is None else value


def finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def integer(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def gate(
    gate_id: str,
    category: str,
    status: str,
    summary: str,
    evidence: dict[str, Any] | None = None,
    *,
    mandatory: bool = True,
) -> dict[str, Any]:
    return {
        "gateId": gate_id,
        "category": category,
        "status": status,
        "mandatory": mandatory,
        "summary": summary,
        "evidence": evidence or {},
    }


def normalized_key(value: Any) -> str:
    return "".join(character for character in str(value).lower() if character.isalnum())


def forbidden_cloud_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        found = {
            str(key) for key in value
            if normalized_key(key) in FORBIDDEN_CLOUD_KEYS
        }
        for child in value.values():
            found.update(forbidden_cloud_keys(child))
        return found
    if isinstance(value, list):
        found: set[str] = set()
        for child in value:
            found.update(forbidden_cloud_keys(child))
        return found
    return set()


def coverage_summary(value: Any) -> dict[str, Any]:
    states = Counter(
        str(item.get("state") or "UNKNOWN").upper()
        for item in (value or {}).values()
        if isinstance(item, dict)
    ) if isinstance(value, dict) else Counter()
    excluded = states["N/A"] + states["NOT_APPLICABLE"] + states["DISABLED"]
    considered = sum(states.values()) - excluded
    percent = round(states["AVAILABLE"] * 100.0 / considered, 2) if considered else None
    return {
        "available": states["AVAILABLE"],
        "partial": states["PARTIAL"],
        "unavailable": states["UNAVAILABLE"] + states["UNKNOWN"],
        "notApplicable": excluded,
        "considered": considered,
        "percent": percent,
    }


def artifact_validation(collection: Path) -> dict[str, Any]:
    validator = Path(__file__).with_name("validate_assessment_artifacts.py")
    try:
        result = subprocess.run(
            [sys.executable, str(validator), str(collection)],
            text=True,
            capture_output=True,
            check=False,
            timeout=90,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"ok": False, "inventory": {}, "errors": ["artifact validator unavailable"]}
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "inventory": {}, "errors": ["artifact validator returned invalid JSON"]}
    if not isinstance(value, dict):
        return {"ok": False, "inventory": {}, "errors": ["artifact validator returned an invalid contract"]}
    return {
        "ok": value.get("ok") is True and result.returncode == 0,
        "inventory": value.get("inventory") if isinstance(value.get("inventory"), dict) else {},
        "errors": [str(item) for item in value.get("errors") or []],
    }


def terminal_gate(metadata: dict[str, Any]) -> dict[str, Any]:
    status = str(metadata.get("status") or "UNKNOWN").upper()
    completed = metadata.get("completed") is True
    passed = status == "COMPLETED" and completed
    return gate(
        "collection.terminal-state",
        "Collection",
        "PASS" if passed else "FAIL",
        "Coleta concluída e marcada como terminal." if passed else "A coleta não está concluída; artefatos parciais não certificam o provider.",
        {"status": status, "completed": completed},
    )


def detection_gate(
    collection: Path,
    expected: str,
    aws: dict[str, Any],
    cloud: dict[str, Any],
    operational: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, str]]:
    sources = {
        "KubernetesEvidence": detected_platform(collection, aws),
        "CloudProviderArtifact": str(cloud.get("provider") or "UNKNOWN"),
        "OperationalInsights": str(
            operational.get("platform")
            or nested(operational, "bestPractices", "platform", fallback="UNKNOWN")
        ),
    }
    mismatches = sorted(name for name, value in sources.items() if value != expected)
    return gate(
        "provider.detection",
        "Provider",
        "PASS" if not mismatches else "FAIL",
        "As fontes independentes concordam com o provider esperado." if not mismatches else "A detecção do provider diverge da expectativa declarada.",
        {"expected": expected, "sources": sources, "mismatchedSources": mismatches},
    ), sources


def readonly_gate(
    comprehensive: dict[str, Any],
    aws: dict[str, Any],
    cloud: dict[str, Any],
    cis: dict[str, Any],
    operational: dict[str, Any],
) -> dict[str, Any]:
    failures: list[str] = []
    reports = {
        "ComprehensiveAssessment": comprehensive,
        "AwsEksAssessment": aws,
        "CloudProviderAssessment": cloud,
        "CisSecurityAssessment": cis,
        "OperationalInsights": operational,
    }
    for name, report in reports.items():
        if report.get("readOnly") is not True:
            failures.append(f"{name}.readOnly")
    for name, report in reports.items():
        safety = report.get("safety") or {}
        if "mutations" in safety and safety.get("mutations") != 0:
            failures.append(f"{name}.mutations")
    kubectl_verbs = set((comprehensive.get("safety") or {}).get("kubectlVerbs") or [])
    if not kubectl_verbs or not kubectl_verbs.issubset({"get", "list"}):
        failures.append("ComprehensiveAssessment.kubectlVerbs")
    operations = set((cloud.get("safety") or {}).get("operations") or [])
    if not operations or not operations.issubset(READ_ONLY_CLOUD_OPERATIONS):
        failures.append("CloudProviderAssessment.operations")
    return gate(
        "safety.read-only",
        "Safety",
        "PASS" if not failures else "FAIL",
        "Todos os contratos declaram somente operações read-only e zero mutações." if not failures else "Um ou mais contratos violam a invariante read-only.",
        {"failures": failures, "cloudOperations": sorted(operations), "kubectlVerbs": sorted(kubectl_verbs)},
    )


def protection_gate(cloud: dict[str, Any], comprehensive: dict[str, Any]) -> dict[str, Any]:
    safety = cloud.get("safety") or {}
    embedded_cloud = comprehensive.get("cloudProvider") or {}
    keys = sorted(forbidden_cloud_keys(cloud) | forbidden_cloud_keys(embedded_cloud))
    failures = []
    if safety.get("credentialsPersisted") is not False:
        failures.append("credentialsPersisted")
    if safety.get("rawPayloadsPersisted") is not False:
        failures.append("rawPayloadsPersisted")
    if safety.get("accountIdentifiers") != "omitted":
        failures.append("accountIdentifiers")
    if keys:
        failures.append("forbiddenCloudKeys")
    return gate(
        "safety.data-protection",
        "Safety",
        "PASS" if not failures else "FAIL",
        "Credenciais, payloads brutos e identificadores de conta estão omitidos." if not failures else "O contrato de proteção de dados do provider não foi comprovado.",
        {"failures": failures, "forbiddenKeys": keys},
    )


def applicability_gate(
    expected: str,
    cloud: dict[str, Any],
    operational: dict[str, Any],
    cis: dict[str, Any],
) -> dict[str, Any]:
    violations: list[str] = []
    managed = {"eks", "aks", "gke"}
    cloud_rules = [item for item in cloud.get("bestPractices") or [] if isinstance(item, dict)]
    if expected == "generic-kubernetes" and cloud_rules:
        violations.append("generic-kubernetes.cloud-rules")
    if expected in managed:
        prefix = f"bestpractice.{expected}."
        if not cloud_rules:
            violations.append("cloud-provider.expected-rules-missing")
        for item in cloud_rules:
            if not str(item.get("ruleId") or "").startswith(prefix):
                violations.append("cloud-provider.foreign-rule")
            if item.get("status") == "PASS" and item.get("applicability") != "APPLICABLE":
                violations.append("cloud-provider.pass-without-applicability")

    operational_rules = [
        item for item in nested(operational, "bestPractices", "rules", fallback=[])
        if isinstance(item, dict)
    ]
    provider_rule_counts = Counter(str(item.get("provider") or "") for item in operational_rules)
    for provider in managed:
        if not provider_rule_counts[provider]:
            violations.append(f"operational.{provider}.rules-missing")
    for item in operational_rules:
        provider = str(item.get("provider") or "")
        status = str(item.get("status") or "")
        applicability = str(item.get("applicability") or "")
        if provider in managed and provider != expected:
            if status != "N/A" or applicability != "NOT_APPLICABLE":
                violations.append(f"operational.{provider}.foreign-rule-active")
        if provider == expected and status == "PASS" and applicability != "APPLICABLE":
            violations.append(f"operational.{provider}.pass-without-evidence")

    controls = [item for item in cis.get("controls") or [] if isinstance(item, dict)]
    by_id = {str(item.get("controlId") or ""): item for item in controls}
    for control_id in CONTROL_PLANE_CONTROLS:
        item = by_id.get(control_id)
        if not item:
            violations.append(f"cis.missing.{control_id}")
            continue
        if expected in managed:
            if (
                item.get("applicability") != "MANAGED_PROVIDER"
                or item.get("status") != "N/A"
                or item.get("managedResponsibility") != "CLOUD_PROVIDER"
            ):
                violations.append(f"cis.managed-responsibility.{control_id}")
        elif item.get("status") == "PASS" or item.get("applicability") == "MANAGED_PROVIDER":
            violations.append(f"cis.generic-control-plane.{control_id}")

    unique = sorted(set(violations))
    return gate(
        "provider.applicability",
        "Provider",
        "PASS" if not unique else "FAIL",
        "Regras e responsabilidades são aplicadas somente ao provider correto." if not unique else "Foram encontradas regras ou responsabilidades aplicadas ao provider incorreto.",
        {"violations": unique, "cloudRules": len(cloud_rules), "operationalRules": len(operational_rules)},
    )


def quality_gate(comprehensive: dict[str, Any]) -> dict[str, Any]:
    quality = comprehensive.get("quality") or {}
    values = {
        "stableIdentityDuplicates": integer(quality.get("stableIdentityDuplicates")),
        "conflictingSeverities": integer(quality.get("conflictingSeverities")),
        "lowConfidencePasses": integer(quality.get("lowConfidencePasses")),
    }
    passed = all(value == 0 for value in values.values())
    return gate(
        "findings.quality",
        "Quality",
        "PASS" if passed else "FAIL",
        "Não há findings duplicados, conflitantes ou PASS sem confiança suficiente." if passed else "O quality gate encontrou inconsistências nos findings.",
        values,
    )


def kubernetes_coverage_gate(comprehensive: dict[str, Any], minimum: float) -> dict[str, Any]:
    summary = coverage_summary(nested(comprehensive, "collection", "resources", fallback={}))
    percent = summary.get("percent")
    if percent is None:
        status, message = "WARN", "A cobertura Kubernetes não está disponível nesta coleta."
    elif percent >= minimum:
        status, message = "PASS", "A cobertura das APIs Kubernetes atingiu o mínimo configurado."
    else:
        status, message = "FAIL", "A cobertura das APIs Kubernetes ficou abaixo do mínimo configurado."
    return gate(
        "coverage.kubernetes",
        "Coverage",
        status,
        message,
        {**summary, "minimumPercent": minimum},
    )


def cloud_evidence_gates(expected: str, cloud: dict[str, Any], minimum: float) -> list[dict[str, Any]]:
    state = str(cloud.get("state") or "UNKNOWN").upper()
    if expected == "generic-kubernetes":
        valid = state in {"N/A", "NOT_APPLICABLE"}
        return [gate(
            "provider.cloud-api",
            "Provider",
            "N/A" if valid else "FAIL",
            "Cloud Provider API não se aplica ao Kubernetes genérico." if valid else "Kubernetes genérico recebeu evidência cloud indevida.",
            {"state": state},
            mandatory=not valid,
        )]

    state_status = "PASS" if state == "AVAILABLE" else "WARN" if state == "PARTIAL" else "FAIL"
    state_gate = gate(
        "provider.cloud-api",
        "Provider",
        state_status,
        "Cloud Provider API read-only disponível." if state_status == "PASS" else "A evidência da Cloud Provider API está parcial ou indisponível.",
        {"state": state, "requests": integer((cloud.get("safety") or {}).get("requests"))},
    )
    summary = coverage_summary(cloud.get("coverage") or {})
    percent = summary.get("percent")
    if percent is None:
        coverage_status, message = "FAIL", "A cobertura da Cloud Provider API não foi comprovada."
    elif percent >= minimum:
        coverage_status, message = "PASS", "A cobertura da Cloud Provider API atingiu o mínimo configurado."
    else:
        coverage_status, message = "FAIL", "A cobertura da Cloud Provider API ficou abaixo do mínimo configurado."
    coverage_gate = gate(
        "coverage.cloud-api",
        "Coverage",
        coverage_status,
        message,
        {**summary, "minimumPercent": minimum},
    )
    return [state_gate, coverage_gate]


def performance_gate(
    metadata: dict[str, Any], comprehensive: dict[str, Any],
    cloud: dict[str, Any], thresholds: Thresholds,
) -> dict[str, Any]:
    budget = nested(comprehensive, "performance", "requestBudget", fallback={}) or {}
    values = {
        "durationSeconds": finite_number(nested(metadata, "performance", "durationSeconds")),
        "apiRequests": finite_number(budget.get("requests")),
        "cloudApiRequests": finite_number((cloud.get("safety") or {}).get("requests")),
        "responseBytes": finite_number(budget.get("responseBytes")),
        "peakRssBytes": finite_number(nested(comprehensive, "performance", "processPeakRssBytes")),
        "retries": finite_number(budget.get("retries")),
        "throttles": finite_number(budget.get("throttles")),
    }
    if values["durationSeconds"] is None:
        values["durationSeconds"] = finite_number(budget.get("elapsedSeconds"))
    limits = {
        "durationSeconds": thresholds.max_duration_seconds,
        "apiRequests": thresholds.max_api_requests,
        "cloudApiRequests": thresholds.max_cloud_api_requests,
        "retries": thresholds.max_api_retries,
        "throttles": thresholds.max_api_throttles,
        "responseBytes": thresholds.max_response_bytes,
        "peakRssBytes": thresholds.max_peak_rss_bytes,
    }
    missing = sorted(key for key in limits if values[key] is None)
    exceeded = sorted(
        key for key, limit in limits.items()
        if values[key] is not None and float(values[key]) > float(limit)
    )
    if exceeded:
        status, message = "FAIL", "A coleta excedeu um ou mais budgets de performance."
    elif missing:
        status, message = "WARN", "A coleta não possui todas as métricas necessárias para comprovar performance."
    else:
        status, message = "PASS", "A coleta permaneceu dentro dos budgets de performance configurados."
    return gate(
        "performance.budget",
        "Performance",
        status,
        message,
        {"values": values, "limits": limits, "missing": missing, "exceeded": exceeded},
    )


def evaluate(collection: Path, expected_provider: str, thresholds: Thresholds | None = None) -> dict[str, Any]:
    thresholds = thresholds or Thresholds()
    collection = collection.resolve()
    if not collection.is_dir():
        raise ValueError("collection directory not found")

    metadata = load(collection / "metadata.json", {}) or {}
    comprehensive = load(collection / "comprehensive-assessment.json", {}) or {}
    aws = load(collection / "aws-eks-assessment.json", {}) or {}
    cloud = load(collection / "cloud-provider-assessment.json", {}) or {}
    cis = load(collection / "cis-security-assessment.json", {}) or {}
    operational = load(collection / "operational-insights.json", {}) or {}
    artifacts = artifact_validation(collection)

    detection, sources = detection_gate(collection, expected_provider, aws, cloud, operational)
    gates = [
        terminal_gate(metadata),
        detection,
        gate(
            "artifacts.integrity",
            "Artifacts",
            "PASS" if artifacts.get("ok") else "FAIL",
            "Artefatos obrigatórios, schemas e sanitização foram validados." if artifacts.get("ok") else "A validação dos artefatos encontrou inconsistências.",
            {"errorCount": len(artifacts.get("errors") or []), "errors": artifacts.get("errors") or []},
        ),
        readonly_gate(comprehensive, aws, cloud, cis, operational),
        protection_gate(cloud, comprehensive),
        applicability_gate(expected_provider, cloud, operational, cis),
        quality_gate(comprehensive),
        kubernetes_coverage_gate(comprehensive, thresholds.min_kubernetes_coverage_percent),
        *cloud_evidence_gates(expected_provider, cloud, thresholds.min_cloud_coverage_percent),
        performance_gate(metadata, comprehensive, cloud, thresholds),
    ]

    counts = Counter(str(item.get("status")) for item in gates)
    mandatory = [item for item in gates if item.get("mandatory")]
    release_ready = all(item.get("status") == "PASS" for item in mandatory)
    state = "FAIL" if any(item.get("status") == "FAIL" for item in mandatory) else "WARN" if any(item.get("status") == "WARN" for item in mandatory) else "PASS"
    return {
        "schemaVersion": "1.0",
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "readOnly": True,
        "notice": "Gate de validação baseado em artefatos sanitizados. Não representa certificação do cloud provider.",
        "collection": {
            "reference": collection.name,
            "status": str(metadata.get("status") or "UNKNOWN").upper(),
        },
        "provider": {
            "expected": expected_provider,
            "detectedSources": sources,
            "cloudEvidenceState": str(cloud.get("state") or "UNKNOWN").upper(),
        },
        "policy": thresholds.as_dict(),
        "summary": {
            "state": state,
            "releaseReady": release_ready,
            "gates": len(gates),
            "status": dict(counts),
        },
        "inventory": artifacts.get("inventory") or {},
        "gates": gates,
    }


def positive_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return number


def percentage(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number < 0 or number > 100:
        raise argparse.ArgumentTypeError("percentage must be between 0 and 100")
    return number


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline release gates for a sanitized provider assessment")
    parser.add_argument("--collection", required=True, type=Path)
    parser.add_argument("--expected-provider", required=True, choices=PROVIDERS)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-duration-seconds", type=positive_float, default=1800.0)
    parser.add_argument("--max-api-requests", type=int, default=1000)
    parser.add_argument("--max-cloud-api-requests", type=int, default=100)
    parser.add_argument("--max-api-retries", type=int, default=3)
    parser.add_argument("--max-api-throttles", type=int, default=0)
    parser.add_argument("--max-response-mb", type=positive_float, default=256.0)
    parser.add_argument("--max-peak-rss-mb", type=positive_float, default=512.0)
    parser.add_argument("--min-kubernetes-coverage-percent", type=percentage, default=80.0)
    parser.add_argument("--min-cloud-coverage-percent", type=percentage, default=80.0)
    args = parser.parse_args()
    if args.max_api_requests <= 0 or args.max_cloud_api_requests < 0 or args.max_api_retries < 0 or args.max_api_throttles < 0:
        parser.error("API limits must be non-negative and --max-api-requests must be greater than zero")
    collection = args.collection.resolve()
    if not collection.is_dir():
        parser.error("collection directory not found")
    output = args.output.resolve() if args.output else collection / "provider-validation.json"
    if not output.parent.is_dir():
        parser.error("output parent directory not found")
    thresholds = Thresholds(
        max_duration_seconds=args.max_duration_seconds,
        max_api_requests=args.max_api_requests,
        max_cloud_api_requests=args.max_cloud_api_requests,
        max_api_retries=args.max_api_retries,
        max_api_throttles=args.max_api_throttles,
        max_response_bytes=int(args.max_response_mb * 1024 * 1024),
        max_peak_rss_bytes=int(args.max_peak_rss_mb * 1024 * 1024),
        min_kubernetes_coverage_percent=args.min_kubernetes_coverage_percent,
        min_cloud_coverage_percent=args.min_cloud_coverage_percent,
    )
    report = evaluate(collection, args.expected_provider, thresholds)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "state": report["summary"]["state"],
        "releaseReady": report["summary"]["releaseReady"],
        "provider": report["provider"]["expected"],
        "gates": report["summary"]["gates"],
        "output": output.name,
    }, ensure_ascii=False))
    return 0 if report["summary"]["releaseReady"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
