#!/usr/bin/env python3
"""Versioned, dependency-free validation for assessment JSON contracts."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "1.0"
SCHEMA_FILES = {
    "metadata.json": "metadata.schema.json",
    "comprehensive-assessment.json": "comprehensive-assessment.schema.json",
    "cis-security-assessment.json": "cis-security-assessment.schema.json",
    "operational-insights.json": "operational-insights.schema.json",
    "provider-validation.json": "provider-validation.schema.json",
    "regression-validation.json": "regression-validation.schema.json",
    "application-manifests-sanitized.json": "application-manifests-sanitized.schema.json",
    "manifest-schema-validation.json": "manifest-schema-validation.schema.json",
    "node-process-evidence.json": "node-process-evidence.schema.json",
    "collector-state.json": "collector-state.schema.json",
}
TERMINAL_REQUIRED = {
    "metadata.json",
    "comprehensive-assessment.json",
    "cis-security-assessment.json",
    "operational-insights.json",
    "application-manifests-sanitized.json",
}


def utc_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def json_type(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "null":
        return value is None
    return True


def validate_value(value: Any, schema: dict[str, Any], pointer: str = "$") -> list[str]:
    """Validate the JSON Schema subset used by this repository."""
    errors: list[str] = []
    if "anyOf" in schema:
        alternatives = [validate_value(value, item, pointer) for item in schema["anyOf"]]
        if not any(not item for item in alternatives):
            errors.append(f"{pointer}: does not match any allowed schema")
        return errors
    expected = schema.get("type")
    if isinstance(expected, list):
        if not any(json_type(value, item) for item in expected):
            errors.append(f"{pointer}: expected one of {expected}, got {type(value).__name__}")
            return errors
    elif isinstance(expected, str) and not json_type(value, expected):
        errors.append(f"{pointer}: expected {expected}, got {type(value).__name__}")
        return errors
    if "const" in schema and value != schema["const"]:
        errors.append(f"{pointer}: expected constant {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{pointer}: value {value!r} is not allowed")
    if isinstance(value, str):
        if len(value) < int(schema.get("minLength", 0)):
            errors.append(f"{pointer}: string is shorter than {schema['minLength']}")
        pattern = schema.get("pattern")
        if pattern and re.search(str(pattern), value) is None:
            errors.append(f"{pointer}: value does not match {pattern!r}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{pointer}: value is below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{pointer}: value is above maximum {schema['maximum']}")
    if isinstance(value, list):
        if len(value) < int(schema.get("minItems", 0)):
            errors.append(f"{pointer}: array has fewer than {schema['minItems']} items")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                errors.extend(validate_value(item, item_schema, f"{pointer}[{index}]"))
    if isinstance(value, dict):
        required = schema.get("required") or []
        for key in required:
            if key not in value:
                errors.append(f"{pointer}: missing required property {key!r}")
        properties = schema.get("properties") or {}
        for key, child in value.items():
            if key in properties and isinstance(properties[key], dict):
                errors.extend(validate_value(child, properties[key], f"{pointer}.{key}"))
            elif schema.get("additionalProperties") is False:
                errors.append(f"{pointer}: additional property {key!r} is not allowed")
    return errors


def default_schema_root() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "schemas"


def validate_document(document: Path, schema_path: Path) -> dict[str, Any]:
    try:
        value = load_json(document)
    except (OSError, json.JSONDecodeError) as error:
        return {"artifact": document.name, "schema": schema_path.name, "state": "FAIL", "errors": [str(error)]}
    try:
        schema = load_json(schema_path)
    except (OSError, json.JSONDecodeError) as error:
        return {"artifact": document.name, "schema": schema_path.name, "state": "FAIL", "errors": [f"invalid schema: {error}"]}
    errors = validate_value(value, schema)
    return {
        "artifact": document.name,
        "schema": schema_path.name,
        "schemaId": schema.get("$id", schema_path.name),
        "state": "PASS" if not errors else "FAIL",
        "errors": errors,
    }


def validate_collection(collection: Path, schema_root: Path | None = None) -> dict[str, Any]:
    collection = collection.resolve()
    schemas = (schema_root or default_schema_root()).resolve()
    results: list[dict[str, Any]] = []
    global_errors: list[str] = []
    metadata: dict[str, Any] = {}
    metadata_path = collection / "metadata.json"
    if metadata_path.is_file():
        try:
            loaded = load_json(metadata_path)
            metadata = loaded if isinstance(loaded, dict) else {}
        except (OSError, json.JSONDecodeError):
            pass
    completed = metadata.get("completed") is True or str(metadata.get("status", "")).upper() == "COMPLETED"
    operational_version = "0"
    try:
        operational = load_json(collection / "operational-insights.json")
        operational_version = str(operational.get("schemaVersion", "0")) if isinstance(operational, dict) else "0"
    except (OSError, json.JSONDecodeError):
        pass
    for artifact, schema_name in SCHEMA_FILES.items():
        document = collection / artifact
        schema_path = schemas / schema_name
        if not schema_path.is_file():
            global_errors.append(f"schema ausente: {schema_name}")
            continue
        if not document.is_file():
            required_new_artifact = completed and artifact == "manifest-schema-validation.json" and operational_version >= "1.3"
            if artifact == "metadata.json" or (completed and artifact in TERMINAL_REQUIRED) or required_new_artifact:
                results.append({"artifact": artifact, "schema": schema_name, "state": "FAIL", "errors": ["artefato obrigatório ausente"]})
            else:
                results.append({"artifact": artifact, "schema": schema_name, "state": "NOT_PRESENT", "errors": []})
            continue
        results.append(validate_document(document, schema_path))
    failed = sum(item["state"] == "FAIL" for item in results) + len(global_errors)
    passed = sum(item["state"] == "PASS" for item in results)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": utc_iso(),
        "readOnly": True,
        "collection": collection.name,
        "summary": {
            "state": "PASS" if failed == 0 else "FAIL",
            "passed": passed,
            "failed": failed,
            "notPresent": sum(item["state"] == "NOT_PRESENT" for item in results),
            "terminalCollection": completed,
        },
        "errors": global_errors,
        "artifacts": results,
    }


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Valida contratos JSON versionados de uma coleta")
    parser.add_argument("--collection", required=True, type=Path)
    parser.add_argument("--schema-root", type=Path)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.collection.is_dir():
        raise SystemExit("collection directory not found")
    report = validate_collection(args.collection, args.schema_root)
    output = args.output or args.collection / "contract-validation.json"
    atomic_write(output, report)
    print(json.dumps({"ok": report["summary"]["state"] == "PASS", "output": str(output), **report["summary"]}, ensure_ascii=False))
    return 0 if report["summary"]["state"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
