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

for item in bin src web deploy docs README.md VERSION; do
  [[ -e "$ROOT/$item" ]] && cp -R "$ROOT/$item" "$STAGING/$NAME/"
done
find "$STAGING/$NAME/bin" "$STAGING/$NAME/src" -type f -name '*.sh' -exec chmod 0755 {} +

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

ARCHIVE="$DESTINATION/$NAME.tar.gz"
tar -C "$STAGING" -czf "$ARCHIVE" "$NAME"
sha256sum "$ARCHIVE" > "$ARCHIVE.sha256"
printf 'Pacote: %s\nChecksum: %s\n' "$ARCHIVE" "$ARCHIVE.sha256"
