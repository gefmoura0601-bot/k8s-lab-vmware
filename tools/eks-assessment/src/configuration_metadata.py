#!/usr/bin/env python3
"""Opt-in, value-free ConfigMap and Secret metadata collector."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any


SENSITIVE_ERROR = re.compile(r"(?i)(authorization|bearer|token|password|secret|credential)(\s*[:=]\s*)\S+")


def utc_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


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


def safe_error(value: str) -> str:
    return SENSITIVE_ERROR.sub(r"\1\2[REDACTED]", value.strip())[:500]


def collect_resource(resource: str, namespace: str, timeout: int) -> dict[str, Any]:
    if not namespace:
        raise ValueError("ConfigMap/Secret metadata exige namespace explícito")
    command = ["kubectl", "get", resource, "-n", namespace]
    command += ["-o", "json", f"--request-timeout={timeout}s"]
    try:
        process = subprocess.run(command, text=True, capture_output=True, timeout=timeout + 10, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"state": "EVIDENCE_UNAVAILABLE", "reason": safe_error(str(error)), "items": []}
    if process.returncode:
        return {"state": "EVIDENCE_UNAVAILABLE", "reason": safe_error(process.stderr or "kubectl falhou"), "items": []}
    try:
        payload = json.loads(process.stdout)
    except json.JSONDecodeError as error:
        return {"state": "EVIDENCE_UNAVAILABLE", "reason": f"resposta Kubernetes inválida: {error}", "items": []}
    kind = "Secret" if resource == "secrets" else "ConfigMap"
    result = []
    for item in payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        metadata = item.get("metadata") or {}
        keys = sorted({
            str(key)
            for field in ("data", "binaryData", "stringData")
            for key in ((item.get(field) or {}).keys() if isinstance(item.get(field), dict) else [])
        })
        row = {
            "kind": kind,
            "namespace": str(metadata.get("namespace") or namespace or "default"),
            "name": str(metadata.get("name") or ""),
            "immutable": item.get("immutable") is True,
            "keys": keys,
        }
        if kind == "Secret":
            row["type"] = str(item.get("type") or "Opaque")
        if row["name"]:
            result.append(row)
    return {"state": "AVAILABLE", "reason": "", "items": sorted(result, key=lambda x: (x["namespace"], x["name"]))}


def collect(namespace: str, include_configmaps: bool, include_secrets: bool, timeout: int) -> dict[str, Any]:
    if not re.fullmatch(r"[a-z0-9]([-a-z0-9.]*[a-z0-9])?", namespace):
        raise ValueError("ConfigMap/Secret metadata exige namespace explícito e válido")
    resources: dict[str, Any] = {}
    all_items: list[dict[str, Any]] = []
    for resource, enabled in (("configmaps", include_configmaps), ("secrets", include_secrets)):
        if not enabled:
            resources[resource] = {"state": "NOT_REQUESTED", "reason": "opt-in desabilitado", "count": 0}
            continue
        value = collect_resource(resource, namespace, timeout)
        all_items.extend(value.pop("items"))
        resources[resource] = {**value, "count": sum(item["kind"] == ("Secret" if resource == "secrets" else "ConfigMap") for item in all_items)}
    states = [value["state"] for value in resources.values() if value["state"] != "NOT_REQUESTED"]
    state = "AVAILABLE" if states and all(item == "AVAILABLE" for item in states) else "PARTIAL" if "AVAILABLE" in states else "EVIDENCE_UNAVAILABLE"
    return {
        "schemaVersion": "1.0",
        "generatedAt": utc_iso(),
        "readOnly": True,
        "state": state,
        "namespaceScope": namespace,
        "policy": {
            "optIn": True,
            "valuesPersisted": False,
            "annotationsPersisted": False,
            "fieldsPersisted": ["kind", "namespace", "name", "type", "immutable", "keys"],
            "rbacWarning": "Kubernetes get/list permite acesso ao objeto completo; use uma identidade temporária e namespaces estritamente necessários.",
        },
        "resources": resources,
        "items": sorted(all_items, key=lambda x: (x["kind"], x["namespace"], x["name"])),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Coleta opt-in de metadados de ConfigMaps e Secrets sem persistir valores")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--namespace", required=True, help="namespace explícito; coleta cluster-wide não é permitida")
    parser.add_argument("--include-configmaps", action="store_true")
    parser.add_argument("--include-secrets", action="store_true")
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args()
    if not args.include_configmaps and not args.include_secrets:
        parser.error("habilite ao menos um profile de metadata")
    if not re.fullmatch(r"[a-z0-9]([-a-z0-9.]*[a-z0-9])?", args.namespace):
        parser.error("namespace inválido")
    if not 5 <= args.timeout <= 300:
        parser.error("timeout deve estar entre 5 e 300")
    report = collect(args.namespace, args.include_configmaps, args.include_secrets, args.timeout)
    atomic_write(args.output, report)
    print(json.dumps({"ok": True, "state": report["state"], "items": len(report["items"]), "output": str(args.output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
