#!/usr/bin/env python3
"""Portable, integrity-checked lifecycle for sanitized assessment collections."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from assessment_contracts import default_schema_root, load_json, validate_collection, validate_value


SCHEMA_VERSION = "1.0"
COLLECTION_ID = re.compile(r"^[A-Za-z0-9._-]+$")
MAX_FILES = 10_000
MAX_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024
EXCLUDED_SUFFIXES = {".log", ".pid", ".lock", ".tmp"}
EXCLUDED_NAMES = {"bundle-manifest.json", "dashboard-token", "kubeconfig"}


def utc_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, value: dict[str, Any]) -> None:
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


def safe_relative(path: PurePosixPath) -> bool:
    return bool(path.parts) and not path.is_absolute() and ".." not in path.parts and "" not in path.parts


def export_files(collection: Path) -> Iterable[Path]:
    for path in sorted(collection.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"symbolic link is not allowed: {path.relative_to(collection)}")
        if not path.is_file():
            continue
        relative = path.relative_to(collection)
        if any(part.startswith(".") for part in relative.parts):
            continue
        if path.name.lower() in EXCLUDED_NAMES or path.suffix.lower() in EXCLUDED_SUFFIXES:
            continue
        yield path


def run_artifact_validation(collection: Path) -> dict[str, Any]:
    validator = Path(__file__).with_name("validate_assessment_artifacts.py")
    try:
        process = subprocess.run(
            [sys.executable, str(validator), str(collection)],
            text=True,
            capture_output=True,
            check=False,
            timeout=120,
        )
        value = json.loads(process.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
        return {"ok": False, "errors": [f"artifact validation unavailable: {error}"]}
    return {
        "ok": process.returncode == 0 and value.get("ok") is True,
        "errors": [str(item) for item in value.get("errors") or []],
        "inventory": value.get("inventory") or {},
    }


def export_bundle(collection: Path, output: Path, tool_version: str) -> dict[str, Any]:
    collection = collection.resolve()
    if not collection.is_dir() or not COLLECTION_ID.fullmatch(collection.name):
        raise ValueError("invalid collection directory")
    contracts = validate_collection(collection)
    artifacts = run_artifact_validation(collection)
    if contracts["summary"]["state"] != "PASS" or not artifacts["ok"]:
        raise ValueError(json.dumps({"contracts": contracts["summary"], "artifactErrors": artifacts["errors"]}, ensure_ascii=False))
    files = list(export_files(collection))
    if len(files) > MAX_FILES:
        raise ValueError("collection has too many files")
    entries = []
    total = 0
    for path in files:
        size = path.stat().st_size
        total += size
        if total > MAX_UNCOMPRESSED_BYTES:
            raise ValueError("collection exceeds portable bundle size limit")
        entries.append({
            "path": path.relative_to(collection).as_posix(),
            "size": size,
            "sha256": sha256_file(path),
        })
    manifest = {
        "schemaVersion": SCHEMA_VERSION,
        "createdAt": utc_iso(),
        "collectionId": collection.name,
        "toolVersion": tool_version,
        "readOnly": True,
        "sanitized": True,
        "validation": {
            "contracts": contracts["summary"],
            "artifacts": {"ok": True, "inventory": artifacts["inventory"]},
        },
        "summary": {"files": len(entries), "bytes": total},
        "files": entries,
    }
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="assessment-bundle-") as temporary:
        staging = Path(temporary) / collection.name
        staging.mkdir()
        for source in files:
            target = staging / source.relative_to(collection)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        atomic_json(staging / "bundle-manifest.json", manifest)
        temporary_archive = output.with_name(f".{output.name}.{os.getpid()}.tmp")
        try:
            with tarfile.open(temporary_archive, "w:gz", format=tarfile.PAX_FORMAT) as archive:
                archive.add(staging, arcname=collection.name, recursive=True)
            os.replace(temporary_archive, output)
        finally:
            temporary_archive.unlink(missing_ok=True)
    return {"ok": True, "bundle": str(output), "collection": collection.name, **manifest["summary"], "sha256": sha256_file(output)}


def inspect_bundle(bundle: Path) -> tuple[dict[str, Any], dict[str, tarfile.TarInfo]]:
    bundle = bundle.resolve()
    members: dict[str, tarfile.TarInfo] = {}
    total = 0
    with tarfile.open(bundle, "r:gz") as archive:
        all_members = archive.getmembers()
        if len(all_members) > MAX_FILES + 100:
            raise ValueError("bundle has too many archive entries")
        roots: set[str] = set()
        for member in all_members:
            pure = PurePosixPath(member.name)
            if not safe_relative(pure):
                raise ValueError(f"unsafe archive path: {member.name}")
            roots.add(pure.parts[0])
            if member.issym() or member.islnk() or member.isdev() or member.isfifo():
                raise ValueError(f"unsupported archive member: {member.name}")
            if member.isfile():
                total += member.size
                if total > MAX_UNCOMPRESSED_BYTES:
                    raise ValueError("bundle exceeds uncompressed size limit")
                members[pure.as_posix()] = member
        if len(roots) != 1:
            raise ValueError("bundle must contain exactly one collection root")
        root = next(iter(roots))
        if not COLLECTION_ID.fullmatch(root):
            raise ValueError("bundle collection id is invalid")
        manifest_name = f"{root}/bundle-manifest.json"
        member = members.get(manifest_name)
        if not member:
            raise ValueError("bundle-manifest.json is missing")
        stream = archive.extractfile(member)
        if stream is None:
            raise ValueError("bundle manifest is unreadable")
        try:
            manifest = json.loads(stream.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(f"bundle manifest is invalid: {error}") from error
        schema = load_json(default_schema_root() / "bundle-manifest.schema.json")
        schema_errors = validate_value(manifest, schema)
        if schema_errors:
            raise ValueError(f"bundle manifest contract failed: {schema_errors[:10]}")
    return manifest, members


def verify_bundle(bundle: Path) -> dict[str, Any]:
    manifest, members = inspect_bundle(bundle)
    collection_id = str(manifest.get("collectionId") or "")
    if not COLLECTION_ID.fullmatch(collection_id):
        raise ValueError("manifest collectionId is invalid")
    expected: dict[str, dict[str, Any]] = {}
    for item in manifest.get("files") or []:
        if not isinstance(item, dict):
            raise ValueError("manifest file entry is invalid")
        relative = PurePosixPath(str(item.get("path") or ""))
        if not safe_relative(relative):
            raise ValueError(f"unsafe manifest path: {relative}")
        expected[f"{collection_id}/{relative.as_posix()}"] = item
    actual = set(members) - {f"{collection_id}/bundle-manifest.json"}
    if actual != set(expected):
        raise ValueError(f"bundle file set differs from manifest: missing={sorted(set(expected) - actual)}, extra={sorted(actual - set(expected))}")
    with tarfile.open(bundle, "r:gz") as archive:
        for name, item in expected.items():
            member = members[name]
            if member.size != int(item.get("size", -1)):
                raise ValueError(f"size mismatch: {name}")
            stream = archive.extractfile(member)
            if stream is None:
                raise ValueError(f"file is unreadable: {name}")
            digest = hashlib.sha256()
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
            if digest.hexdigest() != item.get("sha256"):
                raise ValueError(f"checksum mismatch: {name}")
    return {
        "ok": True,
        "bundle": str(bundle.resolve()),
        "collection": collection_id,
        "files": len(expected),
        "bytes": sum(int(item.get("size", 0)) for item in expected.values()),
        "sha256": sha256_file(bundle),
        "toolVersion": manifest.get("toolVersion"),
    }


def import_bundle(bundle: Path, root: Path) -> dict[str, Any]:
    verification = verify_bundle(bundle)
    collection_id = verification["collection"]
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    destination = root / collection_id
    if destination.exists():
        raise ValueError("destination collection already exists")
    temporary = Path(tempfile.mkdtemp(prefix=f".{collection_id}.", dir=root))
    try:
        with tarfile.open(bundle, "r:gz") as archive:
            for member in archive.getmembers():
                pure = PurePosixPath(member.name)
                if len(pure.parts) == 1:
                    continue
                relative = Path(*pure.parts[1:])
                target = temporary / relative
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if not member.isfile():
                    raise ValueError(f"unsupported archive member: {member.name}")
                target.parent.mkdir(parents=True, exist_ok=True)
                stream = archive.extractfile(member)
                if stream is None:
                    raise ValueError(f"file is unreadable: {member.name}")
                with target.open("wb") as handle:
                    shutil.copyfileobj(stream, handle)
                target.chmod(0o600)
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return {**verification, "importedTo": str(destination)}


def parse_age(value: str) -> dt.timedelta:
    match = re.fullmatch(r"([1-9][0-9]*)([hd])", value)
    if not match:
        raise argparse.ArgumentTypeError("use Nd ou Nh, por exemplo 30d ou 12h")
    count = int(match.group(1))
    return dt.timedelta(days=count) if match.group(2) == "d" else dt.timedelta(hours=count)


def parse_timestamp(value: Any, fallback: float) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)
    except (TypeError, ValueError):
        return dt.datetime.fromtimestamp(fallback, tz=dt.timezone.utc)


def prune(root: Path, age: dt.timedelta, confirm: bool, include_baselines: bool) -> dict[str, Any]:
    root = root.resolve()
    cutoff = dt.datetime.now(dt.timezone.utc) - age
    candidates: list[dict[str, Any]] = []
    if root.is_dir():
        for directory in sorted(root.iterdir()):
            if not directory.is_dir() or directory.is_symlink() or not directory.name.startswith("eks-"):
                continue
            try:
                metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                metadata = {}
            created = parse_timestamp(metadata.get("createdAt"), directory.stat().st_mtime)
            baseline = metadata.get("baseline") is True
            if created < cutoff and (include_baselines or not baseline):
                candidates.append({"id": directory.name, "createdAt": created.isoformat(), "baseline": baseline})
    removed: list[str] = []
    if confirm:
        for item in candidates:
            target = (root / item["id"]).resolve()
            if target.parent != root or not target.name.startswith("eks-"):
                raise ValueError("refusing unsafe prune target")
            shutil.rmtree(target)
            removed.append(item["id"])
    return {
        "ok": True,
        "mode": "CONFIRMED" if confirm else "DRY_RUN",
        "cutoff": cutoff.isoformat(),
        "baselinesProtected": not include_baselines,
        "candidates": candidates,
        "removed": removed,
    }


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Exporta, verifica, importa ou remove coletas portáteis")
    sub = root.add_subparsers(dest="action", required=True)
    export = sub.add_parser("export")
    export.add_argument("--collection", required=True, type=Path)
    export.add_argument("--output", required=True, type=Path)
    export.add_argument("--tool-version", required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--bundle", required=True, type=Path)
    import_command = sub.add_parser("import")
    import_command.add_argument("--bundle", required=True, type=Path)
    import_command.add_argument("--root", required=True, type=Path)
    prune_command = sub.add_parser("prune")
    prune_command.add_argument("--root", required=True, type=Path)
    prune_command.add_argument("--older-than", required=True, type=parse_age)
    prune_command.add_argument("--confirm", action="store_true")
    prune_command.add_argument("--include-baselines", action="store_true")
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        if args.action == "export":
            result = export_bundle(args.collection, args.output, args.tool_version)
        elif args.action == "verify":
            result = verify_bundle(args.bundle)
        elif args.action == "import":
            result = import_bundle(args.bundle, args.root)
        else:
            result = prune(args.root, args.older_than, args.confirm, args.include_baselines)
    except (OSError, ValueError, tarfile.TarError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
