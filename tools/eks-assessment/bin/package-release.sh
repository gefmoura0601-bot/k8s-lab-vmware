#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"
DESTINATION="${1:-$PWD/dist}"
NAME="eks-assessment-${VERSION}"

[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-][A-Za-z0-9]+)*$ ]] || {
  echo "VERSION inválida: $VERSION" >&2
  exit 2
}

mkdir -p "$DESTINATION"
STAGING="$(mktemp -d)"
trap 'rm -rf -- "$STAGING"' EXIT
mkdir -p "$STAGING/$NAME"

for item in src data web deploy docs README.md VERSION CHANGELOG.md; do
  [[ -e "$ROOT/$item" ]] && cp -R "$ROOT/$item" "$STAGING/$NAME/"
done
mkdir -p "$STAGING/$NAME/bin"
cp "$ROOT/bin/eks-assessment.sh" "$ROOT/bin/kubernetes-assessment" "$ROOT/bin/package-release.sh" "$STAGING/$NAME/bin/"
find "$STAGING/$NAME" -type d -name __pycache__ -prune -exec rm -rf -- {} +
find "$STAGING/$NAME" -type f -name '*.pyc' -delete
find "$STAGING/$NAME/bin" -type f -exec chmod 0755 {} +
find "$STAGING/$NAME/src" -type f -name '*.sh' -exec chmod 0755 {} +

SBOM="$STAGING/$NAME/SBOM.spdx"
{
  printf 'SPDXVersion: SPDX-2.3\nDataLicense: CC0-1.0\nSPDXID: SPDXRef-DOCUMENT\n'
  printf 'DocumentName: %s\nDocumentNamespace: https://local.invalid/eks-assessment/%s\n' "$NAME" "$VERSION"
  printf 'Creator: Tool: eks-assessment-package-release\nCreated: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  while IFS= read -r file; do
    relative="${file#"$STAGING/$NAME/"}"
    digest="$(sha256sum "$file" | awk '{print $1}')"
    identifier="$(printf '%s' "$relative" | tr -c 'A-Za-z0-9.-' '-')"
    printf '\nFileName: ./%s\nSPDXID: SPDXRef-File-%s\nFileChecksum: SHA256: %s\nLicenseConcluded: NOASSERTION\n' "$relative" "$identifier" "$digest"
  done < <(find "$STAGING/$NAME" -type f ! -name SBOM.spdx -print | LC_ALL=C sort)
} > "$SBOM"

SBOM_ASSET="$DESTINATION/$NAME.spdx"
cp "$SBOM" "$SBOM_ASSET"

ARCHIVE_NAME="$NAME.tar.gz"
ARCHIVE="$DESTINATION/$ARCHIVE_NAME"
tar -C "$STAGING" -czf "$ARCHIVE" "$NAME"
(
  cd "$DESTINATION"
  sha256sum "$ARCHIVE_NAME" > "$ARCHIVE_NAME.sha256"
)
ARCHIVE_DIGEST="$(sha256sum "$ARCHIVE" | awk '{print $1}')"
SBOM_DIGEST="$(sha256sum "$SBOM_ASSET" | awk '{print $1}')"
SOURCE_COMMIT="${GITHUB_SHA:-}"
if [[ ! "$SOURCE_COMMIT" =~ ^[a-fA-F0-9]{40,64}$ ]]; then
  SOURCE_COMMIT="$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || printf unknown)"
fi
BUILDER=local-shell
[[ "${GITHUB_ACTIONS:-}" == true ]] && BUILDER=github-actions
PROVENANCE="$DESTINATION/$NAME.provenance.json"
printf '{\n  "schemaVersion": "1.0",\n  "subject": {"name": "%s", "sha256": "%s"},\n  "materials": {"sbom": "%s", "sbomSha256": "%s"},\n  "version": "%s",\n  "sourceCommit": "%s",\n  "builder": "%s",\n  "reproducibleInput": true\n}\n' \
  "$ARCHIVE_NAME" "$ARCHIVE_DIGEST" "$(basename "$SBOM_ASSET")" "$SBOM_DIGEST" "$VERSION" "$SOURCE_COMMIT" "$BUILDER" > "$PROVENANCE"

SIGSTORE_BUNDLE=""
if [[ -n "${COSIGN_KEY:-}" ]]; then
  command -v cosign >/dev/null 2>&1 || { echo 'COSIGN_KEY definido, mas cosign não está disponível.' >&2; exit 2; }
  SIGSTORE_BUNDLE="$ARCHIVE.sigstore.json"
  cosign sign-blob --yes --key "$COSIGN_KEY" --bundle "$SIGSTORE_BUNDLE" "$ARCHIVE" >/dev/null
fi

printf 'Pacote: %s\nChecksum: %s\nSBOM: %s\nProveniência: %s\n' \
  "$ARCHIVE" "$ARCHIVE.sha256" "$SBOM_ASSET" "$PROVENANCE"
[[ -z "$SIGSTORE_BUNDLE" ]] || printf 'Sigstore bundle: %s\n' "$SIGSTORE_BUNDLE"
