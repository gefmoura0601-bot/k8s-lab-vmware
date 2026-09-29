#!/usr/bin/env python3
"""Verify portable release archives, checksums, SPDX inventory and attestations."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def expected_checksum(path: Path, archive: Path) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        fields = line.strip().split()
        if len(fields) >= 2 and Path(fields[-1].lstrip("*")).name == archive.name:
            return fields[0].lower()
    raise ValueError("archive checksum entry not found")


def safe_members(archive: tarfile.TarFile) -> tuple[str, list[tarfile.TarInfo]]:
    roots: set[str] = set()
    members = archive.getmembers()
    if len(members) > 20_000:
        raise ValueError("archive has too many entries")
    total = 0
    for member in members:
        path = PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise ValueError(f"unsafe archive path: {member.name}")
        if member.issym() or member.islnk() or member.isdev() or member.isfifo():
            raise ValueError(f"unsupported archive member: {member.name}")
        roots.add(path.parts[0])
        total += max(0, member.size)
        if total > 4 * 1024 * 1024 * 1024:
            raise ValueError("archive exceeds uncompressed size limit")
    if len(roots) != 1:
        raise ValueError("archive must contain exactly one package root")
    return next(iter(roots)), members


def extract_safe(archive_path: Path, destination: Path) -> Path:
    with tarfile.open(archive_path, "r:gz") as archive:
        root, members = safe_members(archive)
        for member in members:
            pure = PurePosixPath(member.name)
            target = destination.joinpath(*pure.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            stream = archive.extractfile(member)
            if stream is None:
                raise ValueError(f"archive member is unreadable: {member.name}")
            with target.open("wb") as handle:
                shutil.copyfileobj(stream, handle)
        return destination / root


def parse_spdx(path: Path) -> dict[str, str]:
    output: dict[str, str] = {}
    filename: str | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("FileName: ./"):
            filename = line[len("FileName: ./"):]
        elif line.startswith("FileChecksum: SHA256: ") and filename:
            output[filename] = line[len("FileChecksum: SHA256: "):].strip().lower()
            filename = None
    if not output:
        raise ValueError("SPDX file inventory is empty")
    return output


def verify_spdx(package_root: Path, external_sbom: Path | None) -> dict[str, Any]:
    internal = package_root / "SBOM.spdx"
    if not internal.is_file():
        raise ValueError("internal SBOM.spdx is missing")
    if external_sbom and external_sbom.read_bytes() != internal.read_bytes():
        raise ValueError("external SBOM differs from packaged SBOM")
    expected = parse_spdx(internal)
    actual = {
        path.relative_to(package_root).as_posix(): sha256(path)
        for path in package_root.rglob("*")
        if path.is_file() and path.name != "SBOM.spdx"
    }
    if set(expected) != set(actual):
        raise ValueError(f"SBOM file set mismatch: missing={sorted(set(actual)-set(expected))}, extra={sorted(set(expected)-set(actual))}")
    invalid = sorted(name for name, digest in actual.items() if expected[name] != digest)
    if invalid:
        raise ValueError(f"SBOM checksum mismatch: {invalid}")
    return {"files": len(actual), "sha256": sha256(internal)}


def verify_provenance(path: Path | None, archive: Path, archive_digest: str, sbom_digest: str) -> dict[str, Any]:
    if path is None:
        return {"state": "NOT_PROVIDED"}
    value = json.loads(path.read_text(encoding="utf-8"))
    subject = value.get("subject") or {}
    materials = value.get("materials") or {}
    if subject.get("name") != archive.name or subject.get("sha256") != archive_digest:
        raise ValueError("provenance subject does not match archive")
    if materials.get("sbomSha256") != sbom_digest:
        raise ValueError("provenance SBOM digest does not match")
    return {"state": "VERIFIED", "builder": value.get("builder"), "sourceCommit": value.get("sourceCommit")}


def verify_signature(archive: Path, bundle: Path | None, certificate_identity: str, oidc_issuer: str) -> dict[str, Any]:
    if bundle is None:
        return {"state": "NOT_PROVIDED"}
    executable = shutil.which("cosign")
    if not executable:
        raise ValueError("cosign is required to verify the supplied Sigstore bundle")
    command = [executable, "verify-blob", "--bundle", str(bundle)]
    if certificate_identity:
        command += ["--certificate-identity", certificate_identity]
    if oidc_issuer:
        command += ["--certificate-oidc-issuer", oidc_issuer]
    command.append(str(archive))
    process = subprocess.run(command, text=True, capture_output=True, timeout=120, check=False)
    if process.returncode:
        raise ValueError(f"Sigstore verification failed: {process.stderr.strip()[:500]}")
    return {"state": "VERIFIED", "type": "Sigstore bundle"}


def evaluate(args: argparse.Namespace) -> dict[str, Any]:
    archive = args.archive.resolve()
    digest = sha256(archive)
    expected = expected_checksum(args.checksum.resolve(), archive)
    if digest != expected:
        raise ValueError("archive checksum mismatch")
    with tempfile.TemporaryDirectory(prefix="assessment-release-") as temporary:
        package_root = extract_safe(archive, Path(temporary))
        version_file = package_root / "VERSION"
        if not version_file.is_file():
            raise ValueError("VERSION is missing from package")
        version = version_file.read_text(encoding="utf-8").strip()
        if package_root.name not in {f"eks-assessment-{version}", f"kubernetes-assessment-{version}"}:
            raise ValueError("package root does not match VERSION")
        sbom = verify_spdx(package_root, args.sbom.resolve() if args.sbom else None)
    provenance = verify_provenance(args.provenance.resolve() if args.provenance else None, archive, digest, sbom["sha256"])
    signature = verify_signature(
        archive,
        args.sigstore_bundle.resolve() if args.sigstore_bundle else None,
        args.certificate_identity,
        args.oidc_issuer,
    )
    if args.require_signature and signature["state"] != "VERIFIED":
        raise ValueError("a verified signature is required")
    return {
        "schemaVersion": "1.0",
        "ok": True,
        "archive": archive.name,
        "version": version,
        "sha256": digest,
        "archiveSafety": "PASS",
        "sbom": {"state": "VERIFIED", **sbom},
        "provenance": provenance,
        "signature": signature,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verifica integridade e supply chain de uma release portátil")
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--checksum", required=True, type=Path)
    parser.add_argument("--sbom", type=Path)
    parser.add_argument("--provenance", type=Path)
    parser.add_argument("--sigstore-bundle", type=Path)
    parser.add_argument("--certificate-identity", default="")
    parser.add_argument("--oidc-issuer", default="")
    parser.add_argument("--require-signature", action="store_true")
    args = parser.parse_args()
    try:
        report = evaluate(args)
    except (OSError, ValueError, tarfile.TarError, json.JSONDecodeError, subprocess.TimeoutExpired) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
