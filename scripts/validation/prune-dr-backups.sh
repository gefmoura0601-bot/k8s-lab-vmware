#!/usr/bin/env bash
set -Eeuo pipefail

BACKUP_ROOT="${BACKUP_ROOT:-/workspace/.dr-backups}"
KEEP="${DR_BACKUP_RETENTION:-5}"
CURRENT="${1:-}"
[[ "${KEEP}" =~ ^[1-9][0-9]*$ ]] || { echo "ERRO: retenção inválida" >&2; exit 1; }

mapfile -t bundles < <(find "${BACKUP_ROOT}" -maxdepth 1 -type f -name 'k8s-lab-dr-*.tar.gz.enc' -printf '%T@ %p\n' | sort -nr | cut -d' ' -f2-)
(( ${#bundles[@]} > KEEP )) || { echo "RETENTION_REMOVED=0"; exit 0; }

removed=0
for bundle in "${bundles[@]:KEEP}"; do
  [[ "${bundle}" != "${CURRENT}" ]] || continue
  report="${bundle%.tar.gz.enc}.report.txt"
  rm -f -- "${bundle}" "${report}"
  echo "RETENTION_REMOVED_FILE=$(basename "${bundle}")"
  ((removed += 1))
done
echo "RETENTION_REMOVED=${removed}"
