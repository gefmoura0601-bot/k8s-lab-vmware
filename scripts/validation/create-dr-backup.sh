#!/usr/bin/env bash
set -Eeuo pipefail

BACKUP_ROOT="${BACKUP_ROOT:-/workspace/.dr-backups}"
PASSPHRASE="${DR_BACKUP_PASSPHRASE:-}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
WORK_DIR="$(mktemp -d)"
BUNDLE="${BACKUP_ROOT}/k8s-lab-dr-${TIMESTAMP}.tar.gz.enc"
REPORT="${BACKUP_ROOT}/k8s-lab-dr-${TIMESTAMP}.report.txt"

cleanup() { find "${WORK_DIR}" -type f -exec shred -u {} + 2>/dev/null || true; rm -rf "${WORK_DIR}"; }
trap cleanup EXIT
fail() { echo "ERRO: $*" >&2; exit 1; }

[[ ${#PASSPHRASE} -ge 20 ]] || fail "DR_BACKUP_PASSPHRASE deve possuir pelo menos 20 caracteres"
mkdir -p "${BACKUP_ROOT}"
chmod 0700 "${BACKUP_ROOT}" "${WORK_DIR}"

POSTGRES_POD="$(kubectl -n databases get pod -l app=postgres -o jsonpath='{.items[0].metadata.name}')"
RABBIT_POD="$(kubectl -n messaging get pod -l app=rabbitmq -o jsonpath='{.items[0].metadata.name}')"
[[ -n "${POSTGRES_POD}" && -n "${RABBIT_POD}" ]] || fail "pods de dados não encontrados"

# Variáveis expandidas dentro do container PostgreSQL.
# shellcheck disable=SC2016
kubectl -n databases exec "${POSTGRES_POD}" -- sh -ec 'PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' >"${WORK_DIR}/postgres.dump"
kubectl -n databases exec "${POSTGRES_POD}" -i -- pg_restore --list <"${WORK_DIR}/postgres.dump" >/dev/null
# Variáveis expandidas dentro do container PostgreSQL.
# shellcheck disable=SC2016
kubectl -n databases exec "${POSTGRES_POD}" -- sh -ec 'PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --data-only --inserts --no-owner --no-privileges' >"${WORK_DIR}/postgres-data.sql"
sed -e '/^--/d' -e '/^\\restrict /d' -e '/^\\unrestrict /d' -e '/^[[:space:]]*$/d' "${WORK_DIR}/postgres-data.sql" | sha256sum | awk '{print $1}' >"${WORK_DIR}/postgres-data.sha256"
kubectl -n databases get secret postgres-secret -o json >"${WORK_DIR}/postgres-secret.json"

kubectl -n messaging exec "${RABBIT_POD}" -- rabbitmqctl export_definitions /tmp/dr-definitions.json >/dev/null
kubectl -n messaging exec "${RABBIT_POD}" -- cat /tmp/dr-definitions.json >"${WORK_DIR}/rabbitmq-definitions.json"
kubectl -n messaging exec "${RABBIT_POD}" -- rm -f /tmp/dr-definitions.json
jq -e '.users and .vhosts and .permissions' "${WORK_DIR}/rabbitmq-definitions.json" >/dev/null

kubectl -n kube-system get secret -l sealedsecrets.bitnami.com/sealed-secrets-key -o json >"${WORK_DIR}/sealed-secrets-keys.json"
kubectl -n argocd get secret -l argocd.argoproj.io/secret-type=repository -o json >"${WORK_DIR}/argocd-repositories.json"
jq -e '.items | length >= 1' "${WORK_DIR}/sealed-secrets-keys.json" >/dev/null || fail "chave Sealed Secrets não encontrada"
jq -e '.items | length >= 1' "${WORK_DIR}/argocd-repositories.json" >/dev/null || fail "Secret de repositório Argo CD não encontrado"

{
  kubectl get nodes -o wide
  echo
  kubectl -n argocd get applications
  echo
  kubectl get pvc -A
  echo
  kubectl get storageclass
} >"${WORK_DIR}/cluster-inventory.txt"

(cd "${WORK_DIR}" && sha256sum postgres.dump postgres-data.sql postgres-secret.json rabbitmq-definitions.json sealed-secrets-keys.json argocd-repositories.json cluster-inventory.txt >SHA256SUMS)
jq -n --arg created "${TIMESTAMP}" --arg revision "$(git -C /workspace rev-parse HEAD 2>/dev/null || echo unknown)" '{schemaVersion:1,createdAt:$created,gitRevision:$revision,encrypted:true,cipher:"aes-256-cbc",kdf:"pbkdf2",iterations:200000}' >"${WORK_DIR}/manifest.json"

tar -C "${WORK_DIR}" -czf - . | openssl enc -aes-256-cbc -salt -pbkdf2 -iter 200000 -pass env:DR_BACKUP_PASSPHRASE -out "${BUNDLE}"
chmod 0600 "${BUNDLE}"
BUNDLE_SHA="$(sha256sum "${BUNDLE}" | awk '{print $1}')"
{
  echo "status=success"
  echo "created_at=${TIMESTAMP}"
  echo "bundle=$(basename "${BUNDLE}")"
  echo "sha256=${BUNDLE_SHA}"
  echo "encrypted=true"
  echo "postgres_dump=validated"
  echo "rabbitmq_definitions=validated"
  echo "sealed_secrets_keys=present"
  echo "argocd_repository_secrets=present"
} >"${REPORT}"
chmod 0644 "${REPORT}"

echo "BACKUP_BUNDLE=${BUNDLE}"
echo "BACKUP_REPORT=${REPORT}"
echo "BACKUP_SHA256=${BUNDLE_SHA}"
