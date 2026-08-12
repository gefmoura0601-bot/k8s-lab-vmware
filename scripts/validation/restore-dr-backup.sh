#!/usr/bin/env bash
set -Eeuo pipefail

BUNDLE="${1:?uso: restore-dr-backup.sh <bundle.enc>}"
MODE="${DR_RESTORE_MODE:-verify}"
CONFIRMATION="${DR_RESTORE_CONFIRMATION:-}"
WORK_DIR="$(mktemp -d)"
ARGO_TIMEOUT="${DR_ARGO_TIMEOUT_SECONDS:-900}"

cleanup() {
  find "${WORK_DIR}" -type f -exec shred -u {} + 2>/dev/null || true
  rm -rf "${WORK_DIR}"
}
trap cleanup EXIT
fail() { echo "ERRO: $*" >&2; exit 1; }
info() { echo "[INFO] $*"; }

[[ "${MODE}" == verify || "${MODE}" == restore ]] || fail "DR_RESTORE_MODE deve ser verify ou restore"
[[ -r "${BUNDLE}" ]] || fail "bundle não encontrado: ${BUNDLE}"
[[ ${#DR_BACKUP_PASSPHRASE} -ge 20 ]] || fail "DR_BACKUP_PASSPHRASE inválida"
if [[ "${MODE}" == restore ]]; then
  [[ "${CONFIRMATION}" == RESTORE-DR-BACKUP ]] || fail "defina DR_RESTORE_CONFIRMATION=RESTORE-DR-BACKUP"
fi
for command in kubectl jq openssl tar sha256sum sed awk; do
  command -v "${command}" >/dev/null || fail "dependência ausente: ${command}"
done

chmod 0700 "${WORK_DIR}"
info "Descriptografando e validando checksums"
openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass env:DR_BACKUP_PASSPHRASE \
  -in "${BUNDLE}" | tar -C "${WORK_DIR}" -xzf -
(cd "${WORK_DIR}" && sha256sum -c SHA256SUMS >/dev/null)
for required in postgres.dump postgres-data.sha256 rabbitmq-definitions.json sealed-secrets-keys.json argocd-repositories.json; do
  [[ -s "${WORK_DIR}/${required}" ]] || fail "item obrigatório ausente: ${required}"
done
jq -e '.items | length > 0' "${WORK_DIR}/sealed-secrets-keys.json" >/dev/null
jq -e '.items | length > 0' "${WORK_DIR}/argocd-repositories.json" >/dev/null
jq -e '.users and .vhosts and .permissions and .queues' "${WORK_DIR}/rabbitmq-definitions.json" >/dev/null
[[ "$(head -c 5 "${WORK_DIR}/postgres.dump")" == PGDMP ]] || fail "dump PostgreSQL inválido"

echo "DR_BUNDLE_STATUS=valid"
echo "DR_SEALED_KEYS=$(jq '.items | length' "${WORK_DIR}/sealed-secrets-keys.json")"
echo "DR_ARGO_REPOSITORIES=$(jq '.items | length' "${WORK_DIR}/argocd-repositories.json")"
echo "DR_RABBITMQ_QUEUES=$(jq '.queues | length' "${WORK_DIR}/rabbitmq-definitions.json")"
[[ "${MODE}" == restore ]] || { echo "DR_RESTORE_STATUS=verify-only"; exit 0; }

info "Restaurando segredos de controle"
kubectl get namespace kube-system >/dev/null
kubectl create namespace argocd --dry-run=client -o yaml | kubectl apply -f - >/dev/null
jq '{apiVersion:"v1",kind:"List",items:[.items[] | del(.metadata.uid,.metadata.resourceVersion,.metadata.creationTimestamp,.metadata.managedFields,.metadata.annotations) | .metadata.namespace="kube-system"]}' "${WORK_DIR}/sealed-secrets-keys.json" | kubectl apply -f - >/dev/null
jq '{apiVersion:"v1",kind:"List",items:[.items[] | del(.metadata.uid,.metadata.resourceVersion,.metadata.creationTimestamp,.metadata.managedFields,.metadata.annotations) | .metadata.namespace="argocd"]}' "${WORK_DIR}/argocd-repositories.json" | kubectl apply -f - >/dev/null
kubectl -n argocd annotate application platform-root argocd.argoproj.io/refresh=hard --overwrite >/dev/null 2>&1 || true

info "Aguardando o GitOps reconstruir os serviços de dados"
kubectl wait --for=condition=Ready nodes --all --timeout=10m >/dev/null
for _ in $(seq 1 $((ARGO_TIMEOUT / 5))); do
  postgres_count="$(kubectl -n databases get pod -l app=postgres --no-headers 2>/dev/null | wc -l)"
  rabbit_count="$(kubectl -n messaging get pod -l app=rabbitmq --no-headers 2>/dev/null | wc -l)"
  (( postgres_count >= 1 && rabbit_count >= 1 )) && break
  sleep 5
done
POSTGRES_POD="$(kubectl -n databases get pod -l app=postgres -o jsonpath='{.items[0].metadata.name}')"
RABBIT_POD="$(kubectl -n messaging get pod -l app=rabbitmq -o jsonpath='{.items[0].metadata.name}')"
[[ -n "${POSTGRES_POD}" && -n "${RABBIT_POD}" ]] || fail "serviços de dados não foram criados"
kubectl -n databases wait --for=condition=Ready "pod/${POSTGRES_POD}" --timeout=5m >/dev/null
kubectl -n messaging wait --for=condition=Ready pod -l app=rabbitmq --timeout=10m >/dev/null

info "Restaurando PostgreSQL"
# shellcheck disable=SC2016
kubectl -n databases exec -i "${POSTGRES_POD}" -- sh -ec 'PGPASSWORD="$POSTGRES_PASSWORD" pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner --no-privileges' <"${WORK_DIR}/postgres.dump"
# shellcheck disable=SC2016
kubectl -n databases exec "${POSTGRES_POD}" -- sh -ec 'PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --data-only --inserts --no-owner --no-privileges' >"${WORK_DIR}/postgres-restored.sql"
EXPECTED="$(<"${WORK_DIR}/postgres-data.sha256")"
ACTUAL="$(sed -e '/^--/d' -e '/^\\restrict /d' -e '/^\\unrestrict /d' -e '/^[[:space:]]*$/d' "${WORK_DIR}/postgres-restored.sql" | sha256sum | awk '{print $1}')"
[[ "${ACTUAL}" == "${EXPECTED}" ]] || fail "hash lógico PostgreSQL diverge"

info "Restaurando definitions RabbitMQ"
kubectl -n messaging cp "${WORK_DIR}/rabbitmq-definitions.json" "${RABBIT_POD}:/tmp/dr-definitions.json"
if ! jq -e '.users[] | select(.name == "guest")' "${WORK_DIR}/rabbitmq-definitions.json" >/dev/null; then
  kubectl -n messaging exec "${RABBIT_POD}" -- rabbitmqctl delete_user guest >/dev/null 2>&1 || true
fi
kubectl -n messaging exec "${RABBIT_POD}" -- rabbitmqctl import_definitions /tmp/dr-definitions.json >/dev/null
kubectl -n messaging exec "${RABBIT_POD}" -- rm -f /tmp/dr-definitions.json

info "Recompondo membros votantes das filas quorum"
RABBIT_REPLICAS="$(kubectl -n messaging get statefulset rabbitmq -o jsonpath='{.spec.replicas}')"
mapfile -t QUORUM_QUEUES < <(jq -r '.queues[] | select(.type == "quorum") | [.vhost,.name] | @tsv' "${WORK_DIR}/rabbitmq-definitions.json")
for queue_record in "${QUORUM_QUEUES[@]}"; do
  IFS=$'\t' read -r vhost queue <<<"${queue_record}"
  for ordinal in $(seq 1 $((RABBIT_REPLICAS - 1))); do
    node="rabbit@rabbitmq-${ordinal}.rabbitmq-headless.messaging.svc.cluster.local"
    if ! kubectl -n messaging exec "${RABBIT_POD}" -- rabbitmq-queues quorum_status --vhost "${vhost}" "${queue}" 2>/dev/null | grep -Fq "${node}"; then
      kubectl -n messaging exec "${RABBIT_POD}" -- rabbitmq-queues add_member --vhost "${vhost}" "${queue}" "${node}" --membership voter --timeout 120 >/dev/null
    fi
  done
  voters="$(kubectl -n messaging exec "${RABBIT_POD}" -- rabbitmq-queues quorum_status --vhost "${vhost}" "${queue}" | grep -c 'voter')"
  [[ "${voters}" == "${RABBIT_REPLICAS}" ]] || fail "fila quorum ${queue} possui ${voters}/${RABBIT_REPLICAS} votantes"
  echo "DR_QUORUM_QUEUE=${vhost}:${queue}:${voters}"
done

info "Validando estado final"
for _ in $(seq 1 60); do
  keda_ready="$(kubectl -n memory-lab get scaledobject memory-worker-rabbitmq -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null || true)"
  [[ "${keda_ready}" == True ]] && break
  sleep 5
done
[[ "${keda_ready}" == True ]] || fail "KEDA memory-worker-rabbitmq não ficou Ready"
kubectl -n argocd annotate application memory-lab argocd.argoproj.io/refresh=hard --overwrite >/dev/null 2>&1 || true
for _ in $(seq 1 60); do
  bad_apps="$(kubectl -n argocd get applications -o json | jq '[.items[] | select(.status.sync.status != "Synced" or .status.health.status != "Healthy")] | length')"
  [[ "${bad_apps}" == 0 ]] && break
  sleep 5
done
[[ "${bad_apps}" == 0 ]] || fail "${bad_apps} aplicações Argo CD fora de Synced/Healthy"
memory_pressure="$(kubectl get nodes -o json | jq '[.items[].status.conditions[] | select(.type == "MemoryPressure" and .status != "False")] | length')"
[[ "${memory_pressure}" == 0 ]] || fail "há nós com MemoryPressure"

echo "DR_POSTGRES_DATA_SHA256=${ACTUAL}"
echo "DR_ARGOCD_UNHEALTHY=${bad_apps}"
echo "DR_MEMORY_PRESSURE_NODES=${memory_pressure}"
echo "DR_RESTORE_STATUS=success"
