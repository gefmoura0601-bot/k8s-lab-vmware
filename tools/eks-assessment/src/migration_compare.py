#!/usr/bin/env python3
"""Cross-cluster and cross-namespace blue-green migration gate."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from blue_green_readiness import generate as generate_readiness, items, load, manual_evidence, nested, object_ref, pod_spec


def utc_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(value)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def normalize_mapping(value: Any = None) -> dict[str, Any]:
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("mapping deve ser um objeto JSON")
    for key in ("namespaceMap", "resourceMap"):
        if not isinstance(value.get(key, {}), dict):
            raise ValueError(f"{key} deve ser um objeto")
    allowed = value.get("allowedDifferences", ["image", "replicas"])
    if not isinstance(allowed, list) or not all(isinstance(item, str) for item in allowed):
        raise ValueError("allowedDifferences deve ser um array de strings")
    return {
        "namespaceMap": {str(key): str(item) for key, item in (value.get("namespaceMap") or {}).items()},
        "resourceMap": {str(key): str(item) for key, item in (value.get("resourceMap") or {}).items()},
        "allowedDifferences": list(dict.fromkeys(allowed)),
    }


def load_mapping(path: Path | None) -> dict[str, Any]:
    return normalize_mapping(load(path, {}) if path else None)


def mapped_namespace(namespace: str, mapping: dict[str, Any]) -> str:
    return str((mapping.get("namespaceMap") or {}).get(namespace) or namespace)


def mapped_resource(kind: str, namespace: str, name: str, mapping: dict[str, Any]) -> tuple[str, str, str]:
    mapped_ns = mapped_namespace(namespace, mapping)
    key = f"{kind}/{namespace}/{name}"
    value = str((mapping.get("resourceMap") or {}).get(key) or name)
    if "/" in value:
        parts = value.split("/")
        if len(parts) == 3:
            return parts[0], parts[1], parts[2]
        if len(parts) == 2:
            return kind, parts[0], parts[1]
    return kind, mapped_ns, value


def workload_inventory(collection: Path) -> dict[tuple[str, str, str], dict[str, Any]]:
    document = load(collection / "application-manifests-sanitized.json", {})
    result: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in items(document):
        namespace, kind, name = object_ref(item)
        if kind not in {"Deployment", "StatefulSet", "DaemonSet", "Rollout", "Job", "CronJob"}:
            continue
        spec = item.get("spec") or {}
        template = pod_spec(item)
        result[(kind, namespace, name)] = {
            "kind": kind, "namespace": namespace, "name": name,
            "replicas": spec.get("replicas"),
            "images": sorted(str(container.get("image") or "") for container in template.get("containers") or []),
            "serviceAccount": str(template.get("serviceAccountName") or "default"),
        }
    return result


def service_inventory(collection: Path) -> dict[tuple[str, str, str], dict[str, Any]]:
    result: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in items(load(collection / "services.json", {})):
        namespace, kind, name = object_ref(item)
        spec = item.get("spec") or {}
        result[(kind, namespace, name)] = {
            "type": str(spec.get("type") or "ClusterIP"),
            "ports": sorted((str(port.get("name") or ""), int(port.get("port") or 0), str(port.get("protocol") or "TCP")) for port in spec.get("ports") or []),
            "headless": spec.get("clusterIP") == "None",
        }
    return result


def resource_identities(collection: Path, filenames: tuple[str, ...]) -> set[tuple[str, str, str]]:
    output = set()
    for filename in filenames:
        for item in items(load(collection / filename, {})):
            namespace, kind, name = object_ref(item)
            output.add((kind, namespace, name))
    return output


def parity(source_values: set[tuple[str, str, str]], target_values: set[tuple[str, str, str]], mapping: dict[str, Any]) -> list[dict[str, str]]:
    missing = []
    for kind, namespace, name in sorted(source_values):
        expected = mapped_resource(kind, namespace, name, mapping)
        if expected not in target_values:
            missing.append({"source": f"{kind}/{namespace}/{name}", "expectedTarget": "/".join(expected)})
    return missing


def target_api_lifecycle_gate(target_operational: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    manifest = target_operational.get("manifestSchema") or {}
    versions = target_operational.get("versions") or {}
    version_summary = versions.get("summary") or {}
    manifest_state = str(manifest.get("state") or "EVIDENCE_UNAVAILABLE")
    evidence = {
        "manifestSchema": manifest_state,
        "endOfSupport": int(version_summary.get("endOfSupport") or 0),
        "nodeVersionSkew": bool(version_summary.get("nodeVersionSkew")),
        "lifecycleUnknown": int(version_summary.get("lifecycleUnknown") or 0),
    }
    if manifest_state == "FAIL" or evidence["endOfSupport"] or evidence["nodeVersionSkew"]:
        return "FAIL", evidence
    if manifest_state in {"EVIDENCE_UNAVAILABLE", "UNKNOWN", ""} or not versions:
        return "UNKNOWN", evidence
    if manifest_state in {"WARN", "PARTIAL"} or evidence["lifecycleUnknown"]:
        return "WARN", evidence
    return "PASS", evidence


def storage_parity(source: Path, target: Path, mapping: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    source_pvcs = items(load(source / "pvcs.json", {}))
    target_pvcs = {(kind, namespace, name) for item in items(load(target / "pvcs.json", {})) for namespace, kind, name in [object_ref(item)]}
    source_pvc_ids = {(kind, namespace, name) for item in source_pvcs for namespace, kind, name in [object_ref(item)]}
    missing_pvcs = parity(source_pvc_ids, target_pvcs, mapping)
    required_classes = {str(nested(item, "spec", "storageClassName")) for item in source_pvcs if nested(item, "spec", "storageClassName")}
    target_classes = {object_ref(item)[2] for item in items(load(target / "storageclasses.json", {}))}
    expected_classes = {mapped_resource("StorageClass", "-", name, mapping)[2] for name in required_classes}
    missing_classes = sorted(expected_classes - target_classes) if (target / "storageclasses.json").is_file() else sorted(expected_classes)
    evidence = {"sourcePVCs": len(source_pvcs), "missingPVCs": missing_pvcs, "requiredStorageClasses": sorted(required_classes), "missingStorageClasses": missing_classes}
    if missing_pvcs or missing_classes:
        return "FAIL", evidence
    if source_pvcs and (not (target / "pvcs.json").is_file() or not (target / "storageclasses.json").is_file()):
        return "UNKNOWN", evidence
    return ("PASS" if source_pvcs else "N/A"), evidence


def workload_identity_gate(target: Path, target_workloads: dict[tuple[str, str, str], dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    required = {(item["namespace"], item["serviceAccount"]) for item in target_workloads.values() if item.get("serviceAccount")}
    if not required:
        return "N/A", {"required": [], "missing": []}
    path = target / "serviceaccounts.json"
    if not path.is_file():
        return "UNKNOWN", {"required": [f"{namespace}/{name}" for namespace, name in sorted(required)], "missing": [], "reason": "serviceaccounts.json ausente"}
    existing = {(object_ref(item)[0], object_ref(item)[2]) for item in items(load(path, {}))}
    missing = [f"{namespace}/{name}" for namespace, name in sorted(required - existing)]
    return ("FAIL" if missing else "PASS"), {"required": [f"{namespace}/{name}" for namespace, name in sorted(required)], "missing": missing}


def normalize_reference(reference: dict[str, Any], mapping: dict[str, Any]) -> tuple[str, str, str, str]:
    target = reference.get("target") or {}
    kind, namespace, name = mapped_resource(str(target.get("kind")), str(target.get("namespace")), str(target.get("name")), mapping)
    return kind, namespace, name, str(target.get("key") or "")


def gate(gate_id: str, domain: str, status: str, summary: str, evidence: Any, mandatory: bool = True) -> dict[str, Any]:
    return {"gateId": gate_id, "domain": domain, "status": status, "mandatory": mandatory, "summary": summary, "evidence": evidence}


def completed(collection: Path) -> bool:
    value = load(collection / "metadata.json", {})
    return value.get("completed") is True and str(value.get("status")) == "COMPLETED"


def evaluate(source: Path, target: Path, mapping: dict[str, Any]) -> dict[str, Any]:
    source = source.resolve(); target = target.resolve()
    if not source.is_dir() or not target.is_dir() or source == target:
        raise ValueError("source e target devem ser diretórios de coleta diferentes")
    for collection in (source, target):
        if not (collection / "blue-green-readiness.json").is_file():
            generate_readiness(collection, include_environment_probes=False)
    source_meta, target_meta = load(source / "metadata.json", {}), load(target / "metadata.json", {})
    source_ready, target_ready = load(source / "blue-green-readiness.json", {}), load(target / "blue-green-readiness.json", {})
    source_workloads, target_workloads = workload_inventory(source), workload_inventory(target)
    missing_workloads = []
    differences = []
    for (kind, namespace, name), old in source_workloads.items():
        target_key = mapped_resource(kind, namespace, name, mapping)
        current = target_workloads.get(target_key)
        if not current:
            missing_workloads.append({"source": f"{kind}/{namespace}/{name}", "expectedTarget": "/".join(target_key)})
            continue
        for field in ("images", "replicas", "serviceAccount"):
            if old.get(field) != current.get(field):
                allowed_fields = mapping.get("allowedDifferences", [])
                allowed = field in allowed_fields or (field == "images" and "image" in allowed_fields)
                differences.append({"resource": f"{kind}/{namespace}/{name}", "field": field, "source": old.get(field), "target": current.get(field), "allowed": allowed})

    source_config = load(source / "configuration-references.json", {})
    target_config = load(target / "configuration-references.json", {})
    source_refs = {normalize_reference(item, mapping) for item in source_config.get("references") or [] if not item.get("optional")}
    target_refs = {(str((item.get("target") or {}).get("kind")), str((item.get("target") or {}).get("namespace")), str((item.get("target") or {}).get("name")), str((item.get("target") or {}).get("key") or "")) for item in target_config.get("references") or [] if not item.get("optional")}
    missing_refs = ["/".join(value) for value in sorted(source_refs - target_refs)]
    unresolved_target = [item for item in target_config.get("references") or [] if not item.get("optional") and item.get("state") in {"MISSING", "EVIDENCE_UNAVAILABLE"}]

    target_traffic = load(target / "traffic-paths.json", {})
    source_traffic = load(source / "traffic-paths.json", {})
    target_state = load(target / "state-data-readiness.json", {})
    target_probes = load(target / "migration-probes.json", {})
    target_operational = load(target / "operational-insights.json", {})
    target_node_state = str(nested(target_operational, "nodeHealth", "state", fallback="EVIDENCE_UNAVAILABLE"))
    manual, manual_state = manual_evidence(target)

    source_services, target_services = service_inventory(source), service_inventory(target)
    missing_services = []
    service_differences = []
    for identity, old in source_services.items():
        expected = mapped_resource(*identity, mapping)
        current = target_services.get(expected)
        if not current:
            missing_services.append({"source": "/".join(identity), "expectedTarget": "/".join(expected)})
            continue
        for field in ("type", "ports", "headless"):
            if old.get(field) != current.get(field):
                service_differences.append({"service": "/".join(identity), "field": field, "source": old.get(field), "target": current.get(field)})
    policy_files = ("networkpolicies.json", "poddisruptionbudgets.json", "hpas.json", "keda-scaledobjects.json", "keda-scaledjobs.json")
    source_policies = resource_identities(source, policy_files)
    target_policies = resource_identities(target, policy_files)
    missing_policies = parity(source_policies, target_policies, mapping)
    storage_status, storage_evidence = storage_parity(source, target, mapping)
    identity_status, identity_evidence = workload_identity_gate(target, target_workloads)
    api_status, api_evidence = target_api_lifecycle_gate(target_operational)
    target_cis = load(target / "cis-security-assessment.json", {})
    cis_controls = target_cis.get("controls") or []
    cis_warnings = sum(item.get("status") in {"WARN", "FAIL"} and item.get("applicability") == "APPLICABLE" for item in cis_controls)
    cis_unknown = sum(item.get("status") in {"UNKNOWN", "PARTIAL"} or item.get("applicability") in {"EVIDENCE_UNAVAILABLE", "MANUAL_REVIEW"} for item in cis_controls)
    security_status = "WARN" if cis_warnings else "UNKNOWN" if not target_cis or cis_unknown else "PASS"
    telemetry = load(target / "prometheus-telemetry.json", {})
    telemetry_state = str(telemetry.get("state") or "EVIDENCE_UNAVAILABLE")
    observability_status = "PASS" if telemetry_state == "AVAILABLE" else "WARN" if telemetry_state == "PARTIAL" else "UNKNOWN"

    gates = [
        gate("migration.collections", "Scope", "PASS" if completed(source) and completed(target) else "FAIL", "Source e target precisam ser coletas COMPLETED; identidades diferentes são permitidas.", {"source": source.name, "target": target.name, "sourceCluster": source_meta.get("clusterName"), "targetCluster": target_meta.get("clusterName"), "sourceNamespace": source_meta.get("namespaceScope"), "targetNamespace": target_meta.get("namespaceScope")}),
        gate("migration.workload-parity", "Workloads", "FAIL" if missing_workloads or any(not item["allowed"] for item in differences) else "WARN" if differences else "PASS", f"Ausentes={len(missing_workloads)}; diferenças={len(differences)}", {"missing": missing_workloads, "differences": differences}),
        gate("migration.service-parity", "Traffic", "FAIL" if missing_services or service_differences else "PASS", f"Services ausentes={len(missing_services)}; diferenças={len(service_differences)}", {"missing": missing_services, "differences": service_differences}, mandatory=bool(source_services)),
        gate("migration.policy-parity", "Resilience & Network", "FAIL" if missing_policies else "PASS" if source_policies else "N/A", f"NetworkPolicy/PDB/autoscaling ausentes={len(missing_policies)}", {"sourceObjects": len(source_policies), "missing": missing_policies}, mandatory=bool(source_policies)),
        gate("migration.workload-identity", "Identity", identity_status, f"ServiceAccounts obrigatórios={len(identity_evidence.get('required') or [])}; ausentes={len(identity_evidence.get('missing') or [])}", identity_evidence, mandatory=identity_status != "N/A"),
        gate("migration.configuration-parity", "Configuration", "FAIL" if missing_refs or any(item.get("state") == "MISSING" for item in unresolved_target) else "UNKNOWN" if any(item.get("state") == "EVIDENCE_UNAVAILABLE" for item in unresolved_target) else "PASS", f"Referências ausentes={len(missing_refs)}; não resolvidas no target={len(unresolved_target)}", {"missingReferences": missing_refs, "unresolvedTarget": unresolved_target[:100]}),
        gate("migration.api-lifecycle", "Compatibility", api_status, f"Manifest schema={api_evidence['manifestSchema']}; EOL={api_evidence['endOfSupport']}; skew={api_evidence['nodeVersionSkew']}", api_evidence),
        gate("migration.traffic-readiness", "Traffic", "FAIL" if target_traffic.get("state") == "FAIL" else "UNKNOWN" if target_traffic.get("state") in {"UNKNOWN", None} or (source_traffic.get("summary", {}).get("paths", 0) and not target_traffic.get("summary", {}).get("paths", 0)) else "WARN" if target_traffic.get("state") == "WARN" else "PASS", f"Target traffic state={target_traffic.get('state', 'UNKNOWN')}", target_traffic.get("summary") or {}),
        gate("migration.storage-parity", "State & Data", storage_status, f"PVCs source={storage_evidence['sourcePVCs']}; PVCs ausentes={len(storage_evidence['missingPVCs'])}; StorageClasses ausentes={len(storage_evidence['missingStorageClasses'])}", storage_evidence, mandatory=bool(storage_evidence["sourcePVCs"])),
        gate("migration.state-data", "State & Data", "FAIL" if target_state.get("state") == "FAIL" else "UNKNOWN" if target_state.get("state") in {"UNKNOWN", None} else "PASS", f"Target state/data={target_state.get('state', 'UNKNOWN')}", target_state.get("summary") or {}, mandatory=bool(target_state.get("summary", {}).get("statefulWorkloads") or target_state.get("summary", {}).get("cronJobsAndJobs"))),
        gate("migration.target-capacity", "Capacity", "PASS" if target_node_state == "PASS" else "FAIL" if target_node_state == "CRIT" else "WARN" if target_node_state == "WARN" else "UNKNOWN", f"Target Node Health={target_node_state}", nested(target_operational, "nodeHealth", "summary", fallback={}) or {}),
        gate("migration.security-posture", "Security", security_status, f"CIS aplicável com alerta={cis_warnings}; evidência incompleta={cis_unknown}", {"controls": len(cis_controls), "warnings": cis_warnings, "unknown": cis_unknown}, mandatory=False),
        gate("migration.observability", "Observability", observability_status, f"Target Prometheus={telemetry_state}", {"prometheusState": telemetry_state}, mandatory=False),
        gate("migration.cutover-probes", "Cutover", "FAIL" if target_probes.get("state") == "FAIL" else "WARN" if target_probes.get("state") == "WARN" else "PASS" if target_probes.get("state") == "PASS" else "UNKNOWN", f"Target probes={target_probes.get('state', 'DISABLED')}", target_probes.get("summary") or {}),
        gate("migration.dns-load-balancer", "Cutover", "PASS" if manual.get("dnsValidated") is True and manual.get("loadBalancerValidated") is True else "FAIL" if manual.get("dnsValidated") is False or manual.get("loadBalancerValidated") is False else "UNKNOWN", "DNS/TTL e load balancer precisam estar aprovados no target.", {"dnsValidated": manual.get("dnsValidated"), "loadBalancerValidated": manual.get("loadBalancerValidated")}),
        gate("migration.rollback", "Rollback", "PASS" if manual.get("rollbackAvailable") is True else "FAIL" if manual.get("rollbackAvailable") is False else "UNKNOWN", "Rollback deve estar comprovado antes do cutover.", {"rollbackAvailable": manual.get("rollbackAvailable"), "pointOfNoReturn": manual.get("pointOfNoReturn")}),
        gate("migration.manual-evidence-contract", "Governance", "FAIL" if manual_state == "INVALID" else "PASS" if manual_state == "VALID" else "UNKNOWN", f"migration-evidence.json={manual_state}", {"state": manual_state}),
    ]
    mandatory = [item for item in gates if item["mandatory"]]
    blockers = [item for item in mandatory if item["status"] == "FAIL"]
    unknown = [item for item in mandatory if item["status"] == "UNKNOWN"]
    status = "NO_GO" if blockers else "UNKNOWN" if unknown else "GO"
    digest = hashlib.sha256((source.name + "\0" + target.name + "\0" + json.dumps(mapping, sort_keys=True)).encode()).hexdigest()
    return {
        "schemaVersion": "1.0", "generatedAt": utc_iso(), "readOnly": True, "status": status,
        "comparisonId": digest[:16],
        "source": {"collection": source.name, "clusterName": source_meta.get("clusterName"), "namespaceScope": source_meta.get("namespaceScope"), "readiness": source_ready.get("status")},
        "target": {"collection": target.name, "clusterName": target_meta.get("clusterName"), "namespaceScope": target_meta.get("namespaceScope"), "readiness": target_ready.get("status")},
        "mapping": mapping,
        "summary": {"gates": len(gates), "blocking": len(blockers), "unknown": len(unknown), "warnings": sum(item["status"] == "WARN" for item in gates), "missingWorkloads": len(missing_workloads), "missingServices": len(missing_services), "missingPolicies": len(missing_policies), "missingConfigurationReferences": len(missing_refs)},
        "gates": gates,
        "notice": "Comparação de migração permite clusters e namespaces diferentes por mapeamento explícito. GO não executa cutover nem substitui aprovação humana.",
    }


def junit(report: dict[str, Any]) -> str:
    suite = ET.Element("testsuite", name="blue-green-migration", tests=str(len(report["gates"])), failures=str(report["summary"]["blocking"]), skipped=str(report["summary"]["unknown"]))
    for item in report["gates"]:
        case = ET.SubElement(suite, "testcase", classname=item["domain"], name=item["gateId"])
        if item["status"] == "FAIL":
            ET.SubElement(case, "failure", message=item["summary"]).text = json.dumps(item.get("evidence") or {}, ensure_ascii=False)
        elif item["status"] == "UNKNOWN":
            ET.SubElement(case, "skipped", message=item["summary"])
    return ET.tostring(suite, encoding="unicode", xml_declaration=True) + "\n"


def sarif(report: dict[str, Any]) -> dict[str, Any]:
    results = []
    for item in report["gates"]:
        if item["status"] not in {"FAIL", "WARN", "UNKNOWN"}:
            continue
        results.append({"ruleId": item["gateId"], "level": "error" if item["status"] == "FAIL" else "warning" if item["status"] == "WARN" else "note", "message": {"text": item["summary"]}, "properties": {"status": item["status"], "domain": item["domain"]}})
    return {"version": "2.1.0", "$schema": "https://json.schemastore.org/sarif-2.1.0.json", "runs": [{"tool": {"driver": {"name": "kubernetes-assessment-blue-green"}}, "results": results}]}


def markdown(report: dict[str, Any]) -> str:
    rows = ["# Blue-Green Migration Gate", "", f"**Status:** {report['status']}", "", f"Source: `{report['source']['collection']}`  ", f"Target: `{report['target']['collection']}`", "", "| Gate | Domínio | Status | Resumo |", "|---|---|---|---|"]
    rows.extend(f"| `{item['gateId']}` | {item['domain']} | **{item['status']}** | {str(item['summary']).replace('|', '/')} |" for item in report["gates"])
    rows.extend(["", report["notice"], ""])
    return "\n".join(rows)


def write_outputs(report: dict[str, Any], output: Path) -> None:
    base = output.with_suffix("") if output.suffix == ".json" else output
    json_path = output if output.suffix == ".json" else Path(str(base) + ".json")
    atomic_json(json_path, report)
    atomic_text(Path(str(base) + ".junit.xml"), junit(report))
    atomic_json(Path(str(base) + ".sarif.json"), sarif(report))
    atomic_text(Path(str(base) + ".md"), markdown(report))


def main() -> int:
    parser = argparse.ArgumentParser(description="Compara source e target para migração blue-green")
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--target", required=True, type=Path)
    parser.add_argument("--mapping", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = evaluate(args.source, args.target, load_mapping(args.mapping))
        output = args.output or args.target / "migration-comparison.json"
        write_outputs(report, output)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps({"ok": report["status"] == "GO", "status": report["status"], "output": str(output), **report["summary"]}, ensure_ascii=False))
    return 0 if report["status"] == "GO" else 1


if __name__ == "__main__":
    raise SystemExit(main())
