#!/usr/bin/env python3
"""Normalized read-only evidence for EKS, AKS and GKE without raw cloud payloads."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

from lifecycle_catalog import assess as assess_lifecycle


READ_ONLY_COMMAND_PREFIXES = (
    ("az", "aks", "show"),
    ("az", "aks", "get-upgrades"),
    ("az", "aks", "nodepool", "list"),
    ("az", "aks", "get-versions"),
    ("gcloud", "container", "clusters", "describe"),
    ("gcloud", "container", "get-server-config"),
)


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


def minor(version: Any) -> str:
    match = re.search(r"(?:^|\D)(1\.\d+)(?:\D|$)", str(version or ""))
    return match.group(1) if match else ""


def detected_platform(directory: Path, aws: dict[str, Any]) -> str:
    nodes = (load(directory / "nodes.json", {}) or {}).get("items") or []
    provider_ids = " ".join(str(nested(item, "spec", "providerID", fallback="")) for item in nodes).lower()
    labels = " ".join(" ".join((nested(item, "metadata", "labels", fallback={}) or {}).keys()) for item in nodes).lower()
    context = str((load(directory / "metadata.json", {}) or {}).get("context") or "").lower()
    if (aws or {}).get("state") not in {None, "", "N/A", "NOT_APPLICABLE"} or "aws:///" in provider_ids or ":eks:" in context:
        return "eks"
    if "azure:///" in provider_ids or "kubernetes.azure.com" in labels or "azmk8s" in context:
        return "aks"
    if "gce://" in provider_ids or "cloud.google.com/gke" in labels or context.startswith("gke_"):
        return "gke"
    return "generic-kubernetes"


def command_json(command: list[str], timeout: int = 45) -> tuple[dict[str, Any], str, str]:
    if not any(tuple(command[:len(prefix)]) == prefix for prefix in READ_ONLY_COMMAND_PREFIXES):
        return {}, "UNAVAILABLE", "comando bloqueado pela allowlist read-only"
    executable = command[0]
    if not shutil.which(executable):
        return {}, "UNAVAILABLE", f"{executable} CLI não está instalada"
    env = {**os.environ, "AWS_PAGER": "", "AZURE_CORE_ONLY_SHOW_ERRORS": "true", "CLOUDSDK_CORE_DISABLE_PROMPTS": "1"}
    try:
        result = subprocess.run(command, text=True, capture_output=True, check=False, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return {}, "UNAVAILABLE", "cloud provider API excedeu o timeout"
    if result.returncode:
        return {}, "UNAVAILABLE", f"cloud provider CLI retornou código {result.returncode}"
    if len(result.stdout.encode("utf-8")) > 16 * 1024 * 1024:
        return {}, "UNAVAILABLE", "resposta do cloud provider excedeu 16 MiB"
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}, "UNAVAILABLE", "cloud provider CLI retornou JSON inválido"
    return value if isinstance(value, dict) else {"items": value}, "AVAILABLE", ""


def rule(rule_id: str, domain: str, status: str, evidence: str, recommendation: str) -> dict[str, Any]:
    available = status not in {"UNKNOWN", "MANUAL", "N/A"}
    return {
        "ruleId": f"bestpractice.{rule_id}", "domain": domain, "status": status,
        "applicability": "APPLICABLE" if available else "EVIDENCE_UNAVAILABLE" if status == "UNKNOWN" else "MANUAL_REVIEW",
        "responsibility": "SHARED", "resource": "cluster", "evidence": evidence,
        "recommendation": recommendation,
    }


def bool_rule(rule_id: str, domain: str, value: Any, available: bool, evidence: str, recommendation: str) -> dict[str, Any]:
    return rule(rule_id, domain, "PASS" if value is True else "WARN" if value is False and available else "UNKNOWN", evidence, recommendation)


def report_base(provider: str) -> dict[str, Any]:
    return {
        "schemaVersion": "1.0", "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "readOnly": True, "provider": provider, "state": "N/A", "reason": "",
        "safety": {"operations": ["get", "list", "describe", "show"], "mutations": 0, "credentialsPersisted": False, "accountIdentifiers": "omitted", "rawPayloadsPersisted": False, "requests": 0},
        "coverage": {}, "cluster": {}, "lifecycle": {}, "bestPractices": [],
    }


def finalize(value: dict[str, Any]) -> dict[str, Any]:
    rows = value.get("bestPractices") or []
    value["summary"] = {"rules": len(rows), "status": dict(Counter(str(x.get("status")) for x in rows)), "coverageAvailable": sum(x.get("state") == "AVAILABLE" for x in (value.get("coverage") or {}).values())}
    return value


def eks_report(directory: Path, aws: dict[str, Any]) -> dict[str, Any]:
    value = report_base("eks")
    if not aws:
        value.update(state="UNAVAILABLE", reason="aws-eks-assessment.json não está disponível")
        return finalize(value)
    inventory = aws.get("inventory") or {}
    cluster = inventory.get("cluster") or {}
    available = aws.get("state") in {"AVAILABLE", "PARTIAL"}
    version = cluster.get("version")
    zones = cluster.get("zones") or nested(inventory, "network", "zones", fallback=[]) or []
    associations = inventory.get("podIdentityAssociations") or []
    addons = inventory.get("addons") or []
    network = inventory.get("network") or {}
    subnets = network.get("subnets") or []
    low_ip = any((item.get("availableIpv4") is not None and int(item.get("availableIpv4")) < 32) or (item.get("availablePercent") is not None and float(item.get("availablePercent")) < 10) for item in subnets)
    value.update(
        state=aws.get("state", "UNAVAILABLE"), reason=aws.get("reason", ""),
        coverage=aws.get("coverage") or {},
        cluster={"version": version or "UNKNOWN", "platformVersion": cluster.get("platformVersion") or "UNKNOWN", "supportType": cluster.get("supportType") or "UNKNOWN", "privateEndpoint": cluster.get("privateEndpoint"), "publicEndpoint": cluster.get("publicEndpoint"), "availabilityZones": len(zones), "managedAddons": len(addons)},
        lifecycle=assess_lifecycle(version, "eks", support_type=str(cluster.get("supportType") or "")),
    )
    value["safety"]["requests"] = int(nested(aws, "safety", "requests", fallback=0) or 0)
    value["bestPractices"] = [
        bool_rule("eks.identity.pod-identity", "Security", bool(associations) or cluster.get("oidcConfigured") is True, available, f"podIdentityAssociations={len(associations)}; oidcConfigured={cluster.get('oidcConfigured')}", "Usar EKS Pod Identity ou IRSA com escopo mínimo."),
        rule("eks.network.ip-capacity", "Networking", "WARN" if low_ip else "PASS" if subnets else "UNKNOWN", f"subnetsAssessed={len(subnets)}; lowCapacity={low_ip}", "Validar capacidade de IP, VPC CNI e prefix delegation."),
        rule("eks.upgrade.addons", "Operations", "PASS" if addons and all(x.get("status") == "ACTIVE" for x in addons) else "WARN" if addons else "UNKNOWN", f"managedAddons={len(addons)}; inactive={sum(x.get('status') != 'ACTIVE' for x in addons)}", "Manter managed add-ons compatíveis com a versão do cluster."),
        rule("eks.reliability.multi-az", "Reliability", "PASS" if len(zones) >= 2 else "WARN" if zones else "UNKNOWN", f"availabilityZones={len(zones)}", "Distribuir nodes e workloads entre Availability Zones."),
    ]
    return finalize(value)


def aks_scope(directory: Path) -> tuple[str, str]:
    metadata = load(directory / "metadata.json", {}) or {}
    name = os.getenv("AKS_CLUSTER_NAME", "").strip()
    group = (os.getenv("AKS_RESOURCE_GROUP") or os.getenv("AZURE_RESOURCE_GROUP") or "").strip()
    candidate = str(metadata.get("clusterName") or "").strip()
    if not name and re.fullmatch(r"[A-Za-z0-9._-]{1,63}", candidate):
        name = candidate
    return name, group


def aks_available_versions(payload: dict[str, Any]) -> list[str]:
    """Normalize current and legacy Azure CLI version response shapes."""
    rows = payload.get("values") or payload.get("orchestrators") or payload.get("items") or []
    available: set[str] = set()
    for item in rows if isinstance(rows, list) else []:
        if not isinstance(item, dict):
            continue
        candidate = str(item.get("version") or item.get("orchestratorVersion") or "").strip()
        if candidate:
            available.add(candidate)
        patches = item.get("patchVersions") or {}
        if isinstance(patches, dict):
            available.update(str(version) for version in patches if version)
    return sorted(available)


def aks_report(directory: Path) -> dict[str, Any]:
    value = report_base("aks")
    name, group = aks_scope(directory)
    if os.getenv("ASSESSMENT_CLOUD_API", "auto").lower() in {"0", "false", "disabled"}:
        value.update(state="DISABLED", reason="cloud provider API desabilitada explicitamente")
        return finalize(value)
    if not name or not group:
        value.update(state="UNAVAILABLE", reason="defina AKS_CLUSTER_NAME e AKS_RESOURCE_GROUP para evidência Azure read-only")
        return finalize(value)
    show, show_state, show_reason = command_json(["az", "aks", "show", "--resource-group", group, "--name", name, "--only-show-errors", "--output", "json"])
    value["safety"]["requests"] += 1
    value["coverage"]["cluster"] = {"state": show_state, **({"reason": show_reason} if show_reason else {})}
    if show_state != "AVAILABLE":
        value.update(state="UNAVAILABLE", reason=show_reason)
        return finalize(value)
    upgrades, upgrades_state, upgrades_reason = command_json(["az", "aks", "get-upgrades", "--resource-group", group, "--name", name, "--only-show-errors", "--output", "json"])
    pools, pools_state, pools_reason = command_json(["az", "aks", "nodepool", "list", "--resource-group", group, "--cluster-name", name, "--only-show-errors", "--output", "json"])
    location = str(show.get("location") or "")
    versions: dict[str, Any] = {}
    versions_state, versions_reason = "UNAVAILABLE", "location não retornada"
    if location:
        versions, versions_state, versions_reason = command_json(["az", "aks", "get-versions", "--location", location, "--only-show-errors", "--output", "json"])
    value["safety"]["requests"] += 2 + int(bool(location))
    value["coverage"].update({
        "upgrades": {"state": upgrades_state, **({"reason": upgrades_reason} if upgrades_reason else {})},
        "nodePools": {"state": pools_state, **({"reason": pools_reason} if pools_reason else {})},
        "regionalVersions": {"state": versions_state, **({"reason": versions_reason} if versions_reason else {})},
    })
    current = str(show.get("currentKubernetesVersion") or show.get("kubernetesVersion") or "UNKNOWN")
    pool_items = pools.get("items") if isinstance(pools.get("items"), list) else pools if isinstance(pools, list) else show.get("agentPoolProfiles") or []
    if isinstance(pool_items, dict): pool_items = []
    upgrade_items = nested(upgrades, "controlPlaneProfile", "upgrades", fallback=[]) or []
    targets = sorted({str(x.get("kubernetesVersion")) for x in upgrade_items if isinstance(x, dict) and x.get("kubernetesVersion")})
    available_versions = aks_available_versions(versions)
    provider_supported = any(minor(x) == minor(current) for x in available_versions) if available_versions else None
    workload_identity = nested(show, "securityProfile", "workloadIdentity", "enabled")
    oidc = nested(show, "oidcIssuerProfile", "enabled")
    azure_rbac = nested(show, "aadProfile", "enableAzureRbac")
    channel = str(nested(show, "autoUpgradeProfile", "upgradeChannel", fallback="") or "")
    system_pools = [x for x in pool_items if isinstance(x, dict) and str(x.get("mode") or "").lower() == "system"]
    value.update(
        state="AVAILABLE" if all(x == "AVAILABLE" for x in (upgrades_state, pools_state, versions_state)) else "PARTIAL",
        cluster={"version": current, "location": location or "UNKNOWN", "privateCluster": nested(show, "apiServerAccessProfile", "enablePrivateCluster"), "workloadIdentity": workload_identity, "oidcIssuer": oidc, "azureRbac": azure_rbac, "upgradeChannel": channel or "UNKNOWN", "nodePools": len(pool_items), "systemNodePools": len(system_pools), "networkPlugin": nested(show, "networkProfile", "networkPlugin", fallback="UNKNOWN"), "outboundType": nested(show, "networkProfile", "outboundType", fallback="UNKNOWN")},
        lifecycle={**assess_lifecycle(current, "aks", provider_supported=provider_supported), "availableUpgrades": targets},
    )
    value["bestPractices"] = [
        bool_rule("aks.identity.workload", "Security", workload_identity is True and oidc is True and azure_rbac is True, True, f"workloadIdentity={workload_identity}; oidcIssuer={oidc}; azureRbac={azure_rbac}", "Habilitar Microsoft Entra Workload ID, OIDC e Azure RBAC conforme aplicável."),
        rule("aks.nodepools.system", "Reliability", "PASS" if system_pools else "WARN" if pools_state == "AVAILABLE" else "UNKNOWN", f"nodePools={len(pool_items)}; systemNodePools={len(system_pools)}", "Manter ao menos um system node pool dedicado e resiliente."),
        rule("aks.upgrade.channels", "Operations", "PASS" if channel.lower() not in {"", "none"} else "WARN", f"upgradeChannel={channel or 'none'}; availableUpgrades={len(targets)}", "Definir estratégia de automatic upgrade e planned maintenance."),
        rule("aks.network.egress", "Networking", "MANUAL", f"networkPlugin={nested(show, 'networkProfile', 'networkPlugin', fallback='UNKNOWN')}; outboundType={nested(show, 'networkProfile', 'outboundType', fallback='UNKNOWN')}", "Validar capacidade de endereços, egress controlado e policies no desenho de rede."),
    ]
    return finalize(value)


def gke_scope(directory: Path) -> tuple[str, str, str]:
    metadata = load(directory / "metadata.json", {}) or {}
    context = str(metadata.get("context") or "")
    name = os.getenv("GKE_CLUSTER_NAME", "").strip()
    location = os.getenv("GKE_LOCATION", "").strip()
    project = (os.getenv("GCP_PROJECT") or os.getenv("GOOGLE_CLOUD_PROJECT") or "").strip()
    match = re.fullmatch(r"gke_([^_]+)_([^_]+)_(.+)", context)
    if match:
        project = project or match.group(1); location = location or match.group(2); name = name or match.group(3)
    return name, location, project


def gke_report(directory: Path) -> dict[str, Any]:
    value = report_base("gke")
    name, location, project = gke_scope(directory)
    if os.getenv("ASSESSMENT_CLOUD_API", "auto").lower() in {"0", "false", "disabled"}:
        value.update(state="DISABLED", reason="cloud provider API desabilitada explicitamente")
        return finalize(value)
    if not all((name, location, project)):
        value.update(state="UNAVAILABLE", reason="defina GKE_CLUSTER_NAME, GKE_LOCATION e GCP_PROJECT para evidência Google Cloud read-only")
        return finalize(value)
    common = ["--location", location, "--project", project, "--format=json", "--quiet"]
    show, show_state, show_reason = command_json(["gcloud", "container", "clusters", "describe", name, *common])
    versions, versions_state, versions_reason = command_json(["gcloud", "container", "get-server-config", *common])
    value["safety"]["requests"] = 2
    value["coverage"] = {
        "cluster": {"state": show_state, **({"reason": show_reason} if show_reason else {})},
        "regionalVersions": {"state": versions_state, **({"reason": versions_reason} if versions_reason else {})},
    }
    if show_state != "AVAILABLE":
        value.update(state="UNAVAILABLE", reason=show_reason)
        return finalize(value)
    current = str(show.get("currentMasterVersion") or "UNKNOWN")
    channel = str(nested(show, "releaseChannel", "channel", fallback="") or "")
    valid = [str(x) for x in (versions.get("validMasterVersions") or [])]
    channel_versions = []
    for entry in versions.get("channels") or []:
        if isinstance(entry, dict): channel_versions.extend(str(x) for x in entry.get("validVersions") or [])
    provider_supported = any(minor(x) == minor(current) for x in valid + channel_versions) if valid or channel_versions else None
    workload_pool = nested(show, "workloadIdentityConfig", "workloadPool", fallback="")
    shielded = nested(show, "shieldedNodes", "enabled")
    private_nodes = nested(show, "privateClusterConfig", "enablePrivateNodes")
    value.update(
        state="AVAILABLE" if versions_state == "AVAILABLE" else "PARTIAL",
        cluster={"version": current, "nodeVersion": show.get("currentNodeVersion") or "UNKNOWN", "location": show.get("location") or location, "releaseChannel": channel or "UNKNOWN", "autopilot": bool(show.get("autopilot")), "workloadIdentity": bool(workload_pool), "shieldedNodes": shielded, "privateNodes": private_nodes, "networkPolicy": nested(show, "networkPolicy", "enabled")},
        lifecycle=assess_lifecycle(current, "gke", release_channel=channel, provider_supported=provider_supported),
    )
    value["lifecycle"]["providerVersionAvailable"] = provider_supported
    value["bestPractices"] = [
        bool_rule("gke.identity.workload", "Security", bool(workload_pool), True, f"workloadIdentityConfigured={bool(workload_pool)}", "Usar Workload Identity Federation for GKE para acesso a Google Cloud APIs."),
        rule("gke.release.channel", "Operations", "PASS" if channel else "WARN", f"releaseChannel={channel or 'none'}", "Inscrever o cluster em um release channel e planejar maintenance windows."),
        bool_rule("gke.shielded.nodes", "Security", shielded, True, f"shieldedNodes={shielded}", "Habilitar Shielded GKE Nodes quando aplicável."),
        bool_rule("gke.network.private", "Networking", private_nodes, True, f"privateNodes={private_nodes}", "Validar private nodes, endpoint exposure, egress e NetworkPolicy."),
    ]
    return finalize(value)


def generate(directory: Path, provider: str = "") -> dict[str, Any]:
    aws = load(directory / "aws-eks-assessment.json", {}) or {}
    detected = provider or detected_platform(directory, aws)
    if detected == "eks": value = eks_report(directory, aws)
    elif detected == "aks": value = aks_report(directory)
    elif detected == "gke": value = gke_report(directory)
    else:
        value = report_base("generic-kubernetes")
        value.update(state="N/A", reason="cluster não identificado como EKS, AKS ou GKE")
        value = finalize(value)
    target = directory / "cloud-provider-assessment.json"
    target.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only normalized EKS/AKS/GKE evidence")
    parser.add_argument("--snapshot-dir", required=True, type=Path)
    parser.add_argument("--provider", choices=("eks", "aks", "gke", "generic-kubernetes"), default="")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    directory = args.snapshot_dir.resolve()
    if not directory.is_dir():
        parser.error("snapshot directory not found")
    result = generate(directory, args.provider)
    if args.output and args.output.resolve() != directory / "cloud-provider-assessment.json":
        args.output.resolve().write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"provider": result["provider"], "state": result["state"], "requests": result["safety"]["requests"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
