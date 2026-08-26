#!/usr/bin/env python3
"""Read-only AWS/EKS control-plane, identity, data-plane and network assessment.

Only AWS list/describe/get operations are used. Credentials, secret values and
account identifiers are never persisted. Missing AWS access is UNKNOWN; a
non-EKS cluster is N/A.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import ipaddress
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1.0"
TRANSIENT = re.compile(r"(?i)(throttl|too many requests|request limit|\b429\b|\b5\d\d\b|timeout|temporar|connection reset)")
SENSITIVE_KEY = re.compile(r"(?i)(secret|token|password|credential|private.?key|session|authorization)")
ACCOUNT_IN_ARN = re.compile(r"(arn:[^:\s]+:[^:\s]*:[^:\s]*:)(\d{12})(:)")
ACCOUNT_ID = re.compile(r"(?<!\d)\d{12}(?!\d)")
SOURCE = "https://docs.aws.amazon.com/eks/latest/best-practices/introduction.html"


def utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def clean_text(value: Any, limit: int = 1200) -> str:
    text = str(value or "")
    text = ACCOUNT_IN_ARN.sub(r"\1<account>\3", text)
    text = ACCOUNT_ID.sub("<account>", text)
    text = re.sub(
        r"(?i)(access[_ -]?key|secret[_ -]?key|session[_ -]?token)\s*[:=]\s*\S+",
        r"\1=<redacted>",
        text,
    )
    return text[:limit] + ("..." if len(text) > limit else "")


def sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "<redacted>" if SENSITIVE_KEY.search(str(key)) else sanitize(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, str):
        return clean_text(value)
    return value


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def nested(value: dict[str, Any], *keys: str, fallback: Any = None) -> Any:
    current: Any = value
    for key in keys:
        if not isinstance(current, dict):
            return fallback
        current = current.get(key)
    return fallback if current is None else current


def issues(value: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for item in value.get("issues") or value.get("health") or []:
        if isinstance(item, dict):
            result.append(clean_text(f"{item.get('code') or item.get('issueCode') or 'issue'}: {item.get('message') or ''}", 300))
        else:
            result.append(clean_text(item, 300))
    return result


def parse_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def flatten(value: Any, prefix: str = "") -> dict[str, str]:
    result: dict[str, str] = {}
    if isinstance(value, dict):
        for key, item in value.items():
            result.update(flatten(item, f"{prefix}.{key}" if prefix else str(key)))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            result.update(flatten(item, f"{prefix}[{index}]"))
    else:
        result[prefix] = str(value)
    return result


class AwsCollector:
    def __init__(
        self,
        cluster: str,
        region: str,
        timeout: int,
        retries: int,
        delay_ms: int,
        max_requests: int,
        account_security: bool,
    ):
        self.cluster = cluster
        self.region = region
        self.timeout = timeout
        self.retries = retries
        self.delay = delay_ms / 1000.0
        self.max_requests = max_requests
        self.account_security = account_security
        self.requests = 0
        self.retry_count = 0
        self.coverage: dict[str, dict[str, Any]] = {}
        self.findings: list[dict[str, Any]] = []
        self.inventory: dict[str, Any] = {}
        self.aws = shutil.which("aws")

    def add(
        self,
        severity: str,
        rule_id: str,
        category: str,
        check: str,
        detail: str,
        recommendation: str = "",
        resource: str = "cluster",
        confidence: str = "HIGH",
        applicability: str = "APPLICABLE",
    ) -> None:
        statuses = {
            "CRIT": "OPEN",
            "WARN": "OPEN",
            "INFO": "REVIEW",
            "PASS": "COMPLIANT",
            "N/A": "NOT_APPLICABLE",
            "UNKNOWN": "UNKNOWN",
            "PARTIAL": "PARTIAL",
        }
        if severity == "N/A":
            applicability = "NOT_APPLICABLE"
        if severity in {"UNKNOWN", "PARTIAL"} and confidence == "HIGH":
            confidence = "LOW"
        resource_key = f"aws|{resource}"
        fingerprint = hashlib.sha256(f"{rule_id}|{resource_key}".encode()).hexdigest()[:16]
        detail = clean_text(detail)
        self.findings.append(
            {
                "id": fingerprint,
                "fingerprint": fingerprint,
                "ruleId": rule_id,
                "resourceKey": resource_key,
                "evidenceHash": hashlib.sha256(detail.encode()).hexdigest()[:16],
                "severity": severity,
                "status": statuses[severity],
                "category": category,
                "check": check,
                "namespace": "-",
                "workload": resource,
                "container": "-",
                "detail": detail,
                "recommendation": recommendation,
                "technology": "Amazon EKS",
                "evidence": "aws-api",
                "confidence": confidence,
                "applicability": applicability,
                "source": SOURCE,
            }
        )

    def call(self, key: str, args: list[str], optional: bool = False) -> dict[str, Any] | None:
        if not self.aws:
            self.coverage[key] = {"state": "UNAVAILABLE", "reason": "AWS CLI is not installed"}
            return None
        if self.requests >= self.max_requests:
            self.coverage[key] = {"state": "PARTIAL", "reason": "AWS request budget exhausted"}
            return None
        command = [self.aws, *args, "--output", "json", "--no-cli-pager"]
        if self.region:
            command += ["--region", self.region]
        last_error = ""
        for attempt in range(self.retries + 1):
            self.requests += 1
            self.retry_count += int(attempt > 0)
            if self.delay:
                time.sleep(self.delay)
            try:
                response = subprocess.run(
                    command,
                    text=True,
                    capture_output=True,
                    timeout=self.timeout,
                    check=False,
                    env={**os.environ, "AWS_PAGER": ""},
                )
            except (OSError, subprocess.TimeoutExpired) as error:
                response = subprocess.CompletedProcess(command, 124, "", str(error))
            if response.returncode == 0:
                try:
                    payload = json.loads(response.stdout or "{}")
                except json.JSONDecodeError:
                    self.coverage[key] = {"state": "UNAVAILABLE", "reason": "AWS CLI returned invalid JSON"}
                    return None
                self.coverage[key] = {"state": "AVAILABLE"}
                return payload if isinstance(payload, dict) else {"items": payload}
            last_error = clean_text(response.stderr or response.stdout or "AWS CLI command failed", 500)
            if attempt >= self.retries or not TRANSIENT.search(last_error):
                break
            time.sleep(min(10.0, 2**attempt))
        lowered = last_error.lower()
        not_applicable = optional and any(
            token in lowered for token in ("unknown operation", "not supported", "resource not found")
        )
        self.coverage[key] = {"state": "N/A" if not_applicable else "UNAVAILABLE", "reason": last_error}
        return None

    def collect_named(
        self,
        list_key: str,
        list_args: list[str],
        field: str,
        describe_prefix: list[str],
        name_option: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        listed = self.call(list_key, list_args, optional=True)
        all_names = list((listed or {}).get(field) or [])
        names = all_names[:limit]
        result: list[dict[str, Any]] = []
        for name in names:
            value = self.call(
                f"{list_key}:{slug(str(name))}",
                [*describe_prefix, name_option, str(name)],
                optional=True,
            )
            if value:
                result.append(value)
        if listed is not None:
            self.coverage[list_key].update(count=len(names), truncated=len(all_names) > limit)
        return result

    def cluster_rules(self, cluster: dict[str, Any]) -> None:
        vpc = cluster.get("resourcesVpcConfig") or {}
        public = bool(vpc.get("endpointPublicAccess"))
        private = bool(vpc.get("endpointPrivateAccess"))
        cidrs = [str(value) for value in vpc.get("publicAccessCidrs") or []]
        world = any(value in {"0.0.0.0/0", "::/0"} for value in cidrs)
        self.add(
            "PASS" if private else "WARN",
            "eks.control-plane.private-endpoint",
            "EKS",
            "Private endpoint",
            f"endpointPrivateAccess={private}",
            "Enable private endpoint access where network architecture permits.",
        )
        self.add(
            "CRIT" if public and world else "WARN" if public else "PASS",
            "eks.control-plane.public-endpoint",
            "EKS",
            "Public endpoint exposure",
            f"endpointPublicAccess={public}; publicCidrs={len(cidrs)}; unrestricted={world}",
            "Disable public access or restrict it to controlled egress CIDRs.",
        )
        encryption = cluster.get("encryptionConfig") or []
        self.add(
            "PASS" if encryption else "WARN",
            "eks.security.secrets-kms",
            "Security",
            "Kubernetes secrets KMS encryption",
            f"encryptionProviders={len(encryption)}",
            "Configure envelope encryption with a customer-managed KMS key.",
        )
        enabled_logs = sorted(
            {
                kind
                for group in cluster.get("logging", {}).get("clusterLogging", [])
                if group.get("enabled")
                for kind in group.get("types") or []
            }
        )
        required = {"api", "audit", "authenticator", "controllerManager", "scheduler"}
        missing = sorted(required - set(enabled_logs))
        self.add(
            "PASS" if not missing else "WARN",
            "eks.observability.control-plane-logs",
            "Observability",
            "Control-plane logs",
            f"enabled={','.join(enabled_logs) or 'none'}; missing={','.join(missing) or 'none'}",
            "Enable and retain all EKS control-plane log types with protected log access.",
        )
        health = issues(cluster.get("health") or {})
        self.add(
            "CRIT" if health else "PASS",
            "eks.control-plane.health",
            "Health",
            "EKS control-plane health",
            "; ".join(health) if health else "No EKS health issues reported.",
            "Resolve EKS health issues before workload or version changes.",
        )
        access = cluster.get("accessConfig") or {}
        auth_mode = str(access.get("authenticationMode") or "UNKNOWN")
        creator_admin = access.get("bootstrapClusterCreatorAdminPermissions")
        self.add(
            "PASS" if auth_mode in {"API", "API_AND_CONFIG_MAP"} else "WARN",
            "eks.identity.access-entries",
            "Identity",
            "Cluster access management",
            f"authenticationMode={auth_mode}",
            "Adopt EKS access entries and phase out legacy aws-auth-only access.",
        )
        self.add(
            "WARN" if creator_admin is True else "PASS" if creator_admin is False else "UNKNOWN",
            "eks.identity.bootstrap-admin",
            "Identity",
            "Bootstrap creator admin",
            f"bootstrapClusterCreatorAdminPermissions={creator_admin}",
            "Remove permanent bootstrap admin and use auditable, scoped access entries.",
        )
        support = str(nested(cluster, "upgradePolicy", "supportType", fallback="UNKNOWN"))
        self.add(
            "WARN" if support == "EXTENDED" else "PASS" if support == "STANDARD" else "UNKNOWN",
            "eks.lifecycle.support-type",
            "Upgrade",
            "Kubernetes support type",
            f"supportType={support}; version={cluster.get('version', 'unknown')}",
            "Keep the cluster on standard support and plan upgrades using EKS Cluster Insights.",
        )
        deletion = cluster.get("deletionProtection")
        self.add(
            "PASS" if deletion is True else "INFO" if deletion is False else "UNKNOWN",
            "eks.reliability.deletion-protection",
            "Reliability",
            "Cluster deletion protection",
            f"deletionProtection={deletion}",
            "Enable deletion protection for production clusters.",
        )
        self.inventory["cluster"] = {
            "name": self.cluster,
            "region": self.region,
            "version": cluster.get("version"),
            "platformVersion": cluster.get("platformVersion"),
            "status": cluster.get("status"),
            "supportType": support,
            "authenticationMode": auth_mode,
            "privateEndpoint": private,
            "publicEndpoint": public,
            "publicCidrs": len(cidrs),
            "zones": [],
            "autoMode": any(bool(cluster.get(key)) for key in ("computeConfig", "storageConfig")),
            "oidcConfigured": bool(nested(cluster, "identity", "oidc", "issuer")),
        }

    def collect_nodegroups(self, cluster_version: str) -> None:
        values = self.collect_named(
            "nodegroups",
            ["eks", "list-nodegroups", "--cluster-name", self.cluster],
            "nodegroups",
            ["eks", "describe-nodegroup", "--cluster-name", self.cluster],
            "--nodegroup-name",
        )
        summaries: list[dict[str, Any]] = []
        asgs: list[str] = []
        for value in values:
            node = value.get("nodegroup") or {}
            name = str(node.get("nodegroupName") or "nodegroup")
            health = issues(node.get("health") or {})
            status = str(node.get("status") or "UNKNOWN")
            version = str(node.get("version") or "UNKNOWN")
            severity = (
                "CRIT"
                if health or status not in {"ACTIVE", "UPDATING"}
                else "WARN"
                if version != cluster_version
                else "PASS"
            )
            self.add(
                severity,
                "eks.data-plane.nodegroup-health",
                "DataPlane",
                "Managed node group health",
                f"status={status}; version={version}; clusterVersion={cluster_version}; issues={'; '.join(health) or 'none'}",
                "Resolve health issues and align node-group and control-plane versions.",
                f"nodegroup/{name}",
            )
            scaling = node.get("scalingConfig") or {}
            update = node.get("updateConfig") or {}
            resources = node.get("resources") or {}
            asgs.extend(
                str(item.get("name"))
                for item in resources.get("autoScalingGroups") or []
                if item.get("name")
            )
            summaries.append(
                {
                    "name": name,
                    "status": status,
                    "version": version,
                    "releaseVersion": node.get("releaseVersion"),
                    "amiType": node.get("amiType"),
                    "capacityType": node.get("capacityType"),
                    "instanceTypes": node.get("instanceTypes") or [],
                    "desired": scaling.get("desiredSize"),
                    "minimum": scaling.get("minSize"),
                    "maximum": scaling.get("maxSize"),
                    "maxUnavailable": update.get("maxUnavailable"),
                    "maxUnavailablePercentage": update.get("maxUnavailablePercentage"),
                    "subnetCount": len(node.get("subnets") or []),
                    "taints": sanitize(node.get("taints") or []),
                    "healthIssues": health,
                }
            )
        state = (self.coverage.get("nodegroups") or {}).get("state")
        if not values:
            self.add(
                "N/A" if state == "N/A" else "INFO" if state == "AVAILABLE" else "UNKNOWN",
                "eks.data-plane.managed-nodegroups",
                "DataPlane",
                "Managed node groups",
                "No managed node group was returned." if state == "AVAILABLE" else f"collectionState={state}",
                "Validate Fargate, Karpenter, Auto Mode or self-managed node lifecycle as applicable.",
            )
        if asgs:
            autoscaling = self.call(
                "autoscaling-groups",
                ["autoscaling", "describe-auto-scaling-groups", "--auto-scaling-group-names", *asgs],
                optional=True,
            )
            self.inventory["autoScalingGroups"] = [
                {
                    "nameHash": hashlib.sha256(
                        str(group.get("AutoScalingGroupName", "")).encode()
                    ).hexdigest()[:12],
                    "minimum": group.get("MinSize"),
                    "maximum": group.get("MaxSize"),
                    "desired": group.get("DesiredCapacity"),
                    "zones": group.get("AvailabilityZones") or [],
                    "instanceCount": len(group.get("Instances") or []),
                }
                for group in (autoscaling or {}).get("AutoScalingGroups") or []
            ]
        self.inventory["nodegroups"] = summaries

    def collect_addons(self) -> None:
        values = self.collect_named(
            "addons",
            ["eks", "list-addons", "--cluster-name", self.cluster],
            "addons",
            ["eks", "describe-addon", "--cluster-name", self.cluster],
            "--addon-name",
        )
        summaries: list[dict[str, Any]] = []
        names: set[str] = set()
        for value in values:
            addon = value.get("addon") or {}
            name = str(addon.get("addonName") or "addon")
            names.add(name)
            health = issues(addon.get("health") or {})
            status = str(addon.get("status") or "UNKNOWN")
            self.add(
                "CRIT" if health or status not in {"ACTIVE", "UPDATING"} else "PASS",
                "eks.addons.health",
                "Addons",
                "EKS managed add-on health",
                f"status={status}; version={addon.get('addonVersion')}; issues={'; '.join(health) or 'none'}",
                "Use a Kubernetes-compatible add-on version and resolve degraded health.",
                f"addon/{name}",
            )
            config = parse_json_object(addon.get("configurationValues"))
            flat = {key.upper(): value for key, value in flatten(config).items()}
            summaries.append(
                {
                    "name": name,
                    "status": status,
                    "version": addon.get("addonVersion"),
                    "healthIssues": health,
                    "configurationKeys": sorted(flat)[:100],
                }
            )
            if name == "vpc-cni":
                prefix = any(
                    key.endswith("ENABLE_PREFIX_DELEGATION") and value.lower() == "true"
                    for key, value in flat.items()
                )
                policy = any(
                    "NETWORK_POLICY" in key and value.lower() not in {"false", "disabled", ""}
                    for key, value in flat.items()
                )
                self.add(
                    "PASS" if prefix else "INFO",
                    "eks.networking.vpc-cni-prefix",
                    "Networking",
                    "VPC CNI prefix delegation",
                    f"configured={prefix}",
                    "Evaluate prefix delegation against subnet capacity and ENI limits.",
                    "addon/vpc-cni",
                )
                self.add(
                    "PASS" if policy else "INFO",
                    "eks.networking.vpc-cni-network-policy",
                    "Networking",
                    "VPC CNI network policy",
                    f"configurationDetected={policy}",
                    "Validate that the selected CNI enforces Kubernetes NetworkPolicy.",
                    "addon/vpc-cni",
                )
        for expected in ("vpc-cni", "coredns", "kube-proxy"):
            if expected not in names:
                self.add(
                    "UNKNOWN",
                    "eks.addons.lifecycle",
                    "Addons",
                    f"{expected} lifecycle",
                    "Not returned as an EKS managed add-on; it may be self-managed or provided by Auto Mode.",
                    "Inventory the running component version and verify compatibility.",
                    f"addon/{expected}",
                )
        self.inventory["addons"] = summaries

    def collect_identity(self, oidc: bool) -> None:
        listed = self.call(
            "access-entries",
            ["eks", "list-access-entries", "--cluster-name", self.cluster],
            optional=True,
        )
        entries = list((listed or {}).get("accessEntries") or [])[:200]
        summaries: list[dict[str, Any]] = []
        for principal in entries:
            principal_hash = hashlib.sha256(str(principal).encode()).hexdigest()[:16]
            described = self.call(
                f"access-entry:{principal_hash}",
                [
                    "eks",
                    "describe-access-entry",
                    "--cluster-name",
                    self.cluster,
                    "--principal-arn",
                    str(principal),
                ],
                optional=True,
            )
            policies = self.call(
                f"access-policies:{principal_hash}",
                [
                    "eks",
                    "list-associated-access-policies",
                    "--cluster-name",
                    self.cluster,
                    "--principal-arn",
                    str(principal),
                ],
                optional=True,
            )
            access = (described or {}).get("accessEntry") or {}
            summaries.append(
                {
                    "principalHash": principal_hash,
                    "type": access.get("type"),
                    "groups": len(access.get("kubernetesGroups") or []),
                    "policyCount": len((policies or {}).get("associatedAccessPolicies") or []),
                }
            )
        state = (self.coverage.get("access-entries") or {}).get("state")
        self.add(
            "PASS" if entries else "INFO" if state == "AVAILABLE" else "UNKNOWN",
            "eks.identity.access-entry-inventory",
            "Identity",
            "EKS access entries",
            f"entries={len(entries)}; state={state}",
            "Review principals, access policies and Kubernetes groups for least privilege.",
        )
        identities = self.call(
            "pod-identities",
            ["eks", "list-pod-identity-associations", "--cluster-name", self.cluster],
            optional=True,
        )
        associations = list((identities or {}).get("associations") or [])
        self.add(
            "PASS" if associations or oidc else "WARN",
            "eks.identity.workload-identity",
            "Identity",
            "Workload AWS identity",
            f"podIdentityAssociations={len(associations)}; oidcIssuerConfigured={oidc}",
            "Use EKS Pod Identity or narrowly scoped IRSA roles.",
        )
        self.inventory["accessEntries"] = summaries
        self.inventory["podIdentityAssociations"] = [
            {
                "namespace": item.get("namespace"),
                "serviceAccount": item.get("serviceAccount"),
                "roleHash": hashlib.sha256(str(item.get("roleArn", "")).encode()).hexdigest()[:16],
            }
            for item in associations[:500]
        ]

    def collect_fargate(self) -> None:
        values = self.collect_named(
            "fargate-profiles",
            ["eks", "list-fargate-profiles", "--cluster-name", self.cluster],
            "fargateProfileNames",
            ["eks", "describe-fargate-profile", "--cluster-name", self.cluster],
            "--fargate-profile-name",
        )
        summaries = []
        for value in values:
            profile = value.get("fargateProfile") or {}
            status = str(profile.get("status") or "UNKNOWN")
            name = str(profile.get("fargateProfileName") or "profile")
            self.add(
                "PASS" if status == "ACTIVE" else "WARN",
                "eks.data-plane.fargate-health",
                "DataPlane",
                "Fargate profile health",
                f"status={status}; selectors={len(profile.get('selectors') or [])}",
                "Resolve non-active Fargate profiles and validate selector coverage.",
                f"fargate/{name}",
            )
            summaries.append(
                {
                    "name": name,
                    "status": status,
                    "selectors": sanitize(profile.get("selectors") or []),
                }
            )
        self.inventory["fargateProfiles"] = summaries

    def collect_insights(self) -> None:
        listed = self.call(
            "cluster-insights",
            ["eks", "list-insights", "--cluster-name", self.cluster],
            optional=True,
        )
        insight_list = list((listed or {}).get("insights") or [])[:200]
        summaries = []
        for insight in insight_list:
            insight_id = str(insight.get("id") or "")
            detail = (
                self.call(
                    f"cluster-insight:{slug(insight_id)}",
                    [
                        "eks",
                        "describe-insight",
                        "--cluster-name",
                        self.cluster,
                        "--id",
                        insight_id,
                    ],
                    optional=True,
                )
                if insight_id
                else None
            )
            value = (detail or {}).get("insight") or insight
            status = str(
                nested(
                    value,
                    "insightStatus",
                    "status",
                    fallback=value.get("status") or "UNKNOWN",
                )
            )
            category = str(value.get("category") or "INSIGHT")
            message = clean_text(
                value.get("description")
                or nested(value, "insightStatus", "reason")
                or value.get("name")
                or "",
                500,
            )
            severity = (
                "CRIT"
                if status in {"ERROR", "FAILED"}
                else "WARN"
                if status in {"WARNING", "UNKNOWN"}
                else "PASS"
            )
            self.add(
                severity,
                "eks.upgrade.cluster-insight",
                "Upgrade",
                "EKS Cluster Insight",
                f"category={category}; status={status}; {message}",
                "Resolve EKS insight findings before upgrades or rollbacks.",
                f"insight/{insight_id or slug(message)}",
            )
            summaries.append(
                {
                    "idHash": hashlib.sha256(insight_id.encode()).hexdigest()[:12]
                    if insight_id
                    else "",
                    "category": category,
                    "status": status,
                    "description": message,
                }
            )
        state = (self.coverage.get("cluster-insights") or {}).get("state")
        if not insight_list:
            self.add(
                "PASS" if state == "AVAILABLE" else "UNKNOWN",
                "eks.upgrade.cluster-insights-coverage",
                "Upgrade",
                "EKS Cluster Insights coverage",
                "No open insight was returned." if state == "AVAILABLE" else f"collectionState={state}",
                "Refresh and review EKS insights before control-plane changes.",
            )
        self.inventory["clusterInsights"] = summaries

    def collect_network(self, cluster: dict[str, Any]) -> None:
        vpc = cluster.get("resourcesVpcConfig") or {}
        subnet_ids = list(vpc.get("subnetIds") or [])
        subnet_data = (
            self.call(
                "subnets",
                ["ec2", "describe-subnets", "--subnet-ids", *subnet_ids],
                optional=True,
            )
            if subnet_ids
            else None
        )
        summaries = []
        zones: set[str] = set()
        low = 0
        for subnet in (subnet_data or {}).get("Subnets") or []:
            zone = str(subnet.get("AvailabilityZone") or "")
            if zone:
                zones.add(zone)
            available = int(subnet.get("AvailableIpAddressCount") or 0)
            try:
                total = (
                    int(
                        ipaddress.ip_network(
                            str(subnet.get("CidrBlock")), strict=False
                        ).num_addresses
                    )
                    - 5
                )
            except ValueError:
                total = 0
            percent = round(100 * available / total, 2) if total > 0 else None
            low += int(available < 32 or (percent is not None and percent < 10))
            summaries.append(
                {
                    "zone": zone,
                    "availableIpv4": available,
                    "availablePercent": percent,
                    "publicIpOnLaunch": bool(subnet.get("MapPublicIpOnLaunch")),
                    "state": subnet.get("State"),
                }
            )
        if subnet_ids:
            state = (self.coverage.get("subnets") or {}).get("state")
            self.add(
                "CRIT" if low else "PASS" if state == "AVAILABLE" else "UNKNOWN",
                "eks.networking.subnet-ip-capacity",
                "Networking",
                "Subnet IP capacity",
                f"subnets={len(subnet_ids)}; assessed={len(summaries)}; lowCapacity={low}; zones={len(zones)}",
                "Increase pod IP capacity or enable compatible prefix delegation before exhaustion.",
            )
            self.add(
                "PASS" if len(zones) >= 2 else "WARN",
                "eks.reliability.subnet-zones",
                "Reliability",
                "EKS subnet failure domains",
                f"availabilityZones={len(zones)}",
                "Use subnets across independent Availability Zones.",
            )
        group_ids = list(
            dict.fromkeys(
                [
                    *(vpc.get("securityGroupIds") or []),
                    *(
                        [vpc.get("clusterSecurityGroupId")]
                        if vpc.get("clusterSecurityGroupId")
                        else []
                    ),
                ]
            )
        )
        groups = (
            self.call(
                "security-groups",
                ["ec2", "describe-security-groups", "--group-ids", *group_ids],
                optional=True,
            )
            if group_ids
            else None
        )
        exposures = 0
        for group in (groups or {}).get("SecurityGroups") or []:
            for permission in group.get("IpPermissions") or []:
                cidrs = [
                    item.get("CidrIp") for item in permission.get("IpRanges") or []
                ] + [
                    item.get("CidrIpv6")
                    for item in permission.get("Ipv6Ranges") or []
                ]
                exposures += int(
                    any(value in {"0.0.0.0/0", "::/0"} for value in cidrs)
                )
        if group_ids:
            self.add(
                "WARN" if exposures else "PASS" if groups is not None else "UNKNOWN",
                "eks.networking.security-group-exposure",
                "Security",
                "Cluster security-group exposure",
                f"groups={len(group_ids)}; worldOpenIngressRules={exposures}",
                "Restrict cluster and node security-group ingress.",
            )
        self.inventory["network"] = {
            "subnets": summaries,
            "zones": sorted(zones),
            "securityGroupCount": len(group_ids),
            "worldOpenIngressRules": exposures,
        }
        if isinstance(self.inventory.get("cluster"), dict):
            self.inventory["cluster"]["zones"] = sorted(zones)

    def collect_guardduty(self) -> None:
        if not self.account_security:
            self.add(
                "UNKNOWN",
                "eks.security.runtime-monitoring",
                "Security",
                "Runtime threat detection",
                "Account-wide GuardDuty inspection was not enabled for this assessment profile.",
                "Run with --include-account-security or validate coverage in the security account.",
            )
            return
        detectors = self.call(
            "guardduty-detectors", ["guardduty", "list-detectors"], optional=True
        )
        detector_ids = list((detectors or {}).get("DetectorIds") or [])
        enabled = False
        features: list[str] = []
        for detector_id in detector_ids[:5]:
            value = self.call(
                f"guardduty:{hashlib.sha256(str(detector_id).encode()).hexdigest()[:8]}",
                ["guardduty", "get-detector", "--detector-id", str(detector_id)],
                optional=True,
            )
            enabled |= str((value or {}).get("Status")) == "ENABLED"
            features.extend(
                str(feature.get("Name"))
                for feature in (value or {}).get("Features") or []
                if feature.get("Status") == "ENABLED"
            )
        runtime = any(
            "RUNTIME" in feature.upper() or "EKS" in feature.upper()
            for feature in features
        )
        self.add(
            "PASS" if enabled and runtime else "WARN" if detector_ids else "UNKNOWN",
            "eks.security.runtime-monitoring",
            "Security",
            "Runtime threat detection",
            f"detectors={len(detector_ids)}; enabled={enabled}; runtimeFeatureDetected={runtime}",
            "Enable and verify EKS Runtime Monitoring coverage.",
            confidence="MEDIUM",
        )

    def result(self) -> dict[str, Any]:
        if not self.cluster:
            self.add(
                "N/A",
                "eks.applicability.cluster",
                "EKS",
                "Amazon EKS applicability",
                "No EKS cluster name could be derived from explicit configuration or kube context.",
                "Provide --cluster/EKS_CLUSTER_NAME for EKS; generic Kubernetes checks remain applicable.",
            )
            return self.output("N/A", "cluster is not identified as Amazon EKS")
        response = self.call(
            "cluster", ["eks", "describe-cluster", "--name", self.cluster]
        )
        cluster = (response or {}).get("cluster") or {}
        if not cluster:
            reason = (self.coverage.get("cluster") or {}).get(
                "reason", "EKS cluster metadata unavailable"
            )
            self.add(
                "UNKNOWN",
                "eks.collection.control-plane",
                "EKS",
                "AWS EKS control-plane collection",
                reason,
                "Configure read-only AWS permissions and verify cluster name/region.",
            )
            return self.output("UNAVAILABLE", reason)
        if not self.region:
            match = re.match(
                r"arn:[^:]+:eks:([^:]+):", str(cluster.get("arn") or "")
            )
            if match:
                self.region = match.group(1)
        self.cluster_rules(cluster)
        self.collect_nodegroups(str(cluster.get("version") or ""))
        self.collect_addons()
        self.collect_fargate()
        self.collect_identity(bool(nested(cluster, "identity", "oidc", "issuer")))
        self.collect_insights()
        self.collect_network(cluster)
        self.collect_guardduty()
        unavailable = sum(
            item.get("state") == "UNAVAILABLE" for item in self.coverage.values()
        )
        partial = sum(
            item.get("state") == "PARTIAL" for item in self.coverage.values()
        )
        return self.output("PARTIAL" if unavailable or partial else "AVAILABLE", "")

    def output(self, state: str, reason: str) -> dict[str, Any]:
        order = {
            "CRIT": 0,
            "WARN": 1,
            "UNKNOWN": 2,
            "PARTIAL": 3,
            "INFO": 4,
            "PASS": 5,
            "N/A": 6,
        }
        self.findings.sort(
            key=lambda item: (
                order.get(item["severity"], 9),
                item["ruleId"],
                item["resourceKey"],
            )
        )
        counts = {
            key: sum(item["severity"] == key for item in self.findings)
            for key in order
        }
        return {
            "schemaVersion": SCHEMA_VERSION,
            "generatedAt": utcnow(),
            "readOnly": True,
            "state": state,
            "reason": clean_text(reason),
            "clusterName": self.cluster or "-",
            "region": self.region or "-",
            "safety": {
                "awsOperations": ["list", "describe", "get"],
                "mutations": 0,
                "credentialsPersisted": False,
                "accountIdentifiers": "redacted",
                "requests": self.requests,
                "retries": self.retry_count,
                "maxRequests": self.max_requests,
            },
            "summary": {
                "checks": len(self.findings),
                "critical": counts["CRIT"],
                "warnings": counts["WARN"],
                "unknown": counts["UNKNOWN"],
                "partial": counts["PARTIAL"],
                "passed": counts["PASS"],
                "notApplicable": counts["N/A"],
                "coverageAvailable": sum(
                    item.get("state") == "AVAILABLE"
                    for item in self.coverage.values()
                ),
                "coverageUnavailable": sum(
                    item.get("state") == "UNAVAILABLE"
                    for item in self.coverage.values()
                ),
            },
            "coverage": sanitize(self.coverage),
            "findings": self.findings,
            "inventory": sanitize(self.inventory),
        }


def detect_context() -> tuple[str, str]:
    cluster = os.getenv("EKS_CLUSTER_NAME", "").strip()
    region = os.getenv(
        "AWS_REGION", os.getenv("AWS_DEFAULT_REGION", "")
    ).strip()
    if cluster:
        return cluster, region
    try:
        context = subprocess.run(
            ["kubectl", "config", "current-context"],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        ).stdout.strip()
        reference = subprocess.run(
            [
                "kubectl",
                "config",
                "view",
                "--minify",
                "-o",
                "jsonpath={.contexts[0].context.cluster}",
            ],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        ).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return "", region
    for value in (reference, context):
        match = re.search(
            r"arn:[^:]+:eks:([^:]+):\d{12}:cluster/([^\s]+)", value
        )
        if match:
            return match.group(2), region or match.group(1)
    return "", region


def main() -> int:
    detected_cluster, detected_region = detect_context()
    parser = argparse.ArgumentParser(description="Read-only AWS/EKS assessment")
    parser.add_argument("--cluster", default=detected_cluster)
    parser.add_argument("--region", default=detected_region)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--api-delay-ms", type=int, default=100)
    parser.add_argument("--max-requests", type=int, default=500)
    parser.add_argument("--include-account-security", action="store_true")
    args = parser.parse_args()
    if not 5 <= args.timeout <= 300:
        parser.error("--timeout must be between 5 and 300")
    if not 0 <= args.retries <= 8:
        parser.error("--retries must be between 0 and 8")
    if not 0 <= args.api_delay_ms <= 5000:
        parser.error("--api-delay-ms must be between 0 and 5000")
    if not 10 <= args.max_requests <= 5000:
        parser.error("--max-requests must be between 10 and 5000")
    collector = AwsCollector(
        args.cluster.strip(),
        args.region.strip(),
        args.timeout,
        args.retries,
        args.api_delay_ms,
        args.max_requests,
        args.include_account_security,
    )
    result = collector.result()
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        path = args.output.resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered, encoding="utf-8")
        print(
            json.dumps(
                {"output": str(path), "state": result["state"], **result["summary"]},
                ensure_ascii=False,
            )
        )
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main())
