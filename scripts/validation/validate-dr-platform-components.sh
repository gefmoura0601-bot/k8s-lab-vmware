#!/usr/bin/env bash
set -Eeuo pipefail

BUNDLE="${1:?uso: validate-dr-platform-components.sh <bundle.enc>}"
NAMESPACE="${DR_PLATFORM_NAMESPACE:-dr-platform-validation}"
WORK_DIR="$(mktemp -d)"

cleanup() {
  kubectl delete namespace "${NAMESPACE}" --ignore-not-found --wait=false >/dev/null 2>&1 || true
  find "${WORK_DIR}" -type f -exec shred -u {} + 2>/dev/null || true
  rm -rf "${WORK_DIR}"
}
trap cleanup EXIT
fail() { echo "ERRO: $*" >&2; exit 1; }

[[ -r "${BUNDLE}" ]] || fail "bundle não encontrado"
[[ ${#DR_BACKUP_PASSPHRASE} -ge 20 ]] || fail "DR_BACKUP_PASSPHRASE inválida"
chmod 0700 "${WORK_DIR}"
openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass env:DR_BACKUP_PASSPHRASE -in "${BUNDLE}" | tar -C "${WORK_DIR}" -xzf -
(cd "${WORK_DIR}" && sha256sum -c SHA256SUMS >/dev/null)

key_count="$(jq '.items | length' "${WORK_DIR}/sealed-secrets-keys.json")"
(( key_count >= 1 )) || fail "nenhuma chave Sealed Secrets"
for index in $(seq 0 $((key_count - 1))); do
  jq -r ".items[${index}].data[\"tls.crt\"]" "${WORK_DIR}/sealed-secrets-keys.json" | base64 -d >"${WORK_DIR}/sealed-${index}.crt"
  jq -r ".items[${index}].data[\"tls.key\"]" "${WORK_DIR}/sealed-secrets-keys.json" | base64 -d >"${WORK_DIR}/sealed-${index}.key"
  cert_hash="$(openssl x509 -in "${WORK_DIR}/sealed-${index}.crt" -pubkey -noout | sha256sum | awk '{print $1}')"
  key_hash="$(openssl pkey -in "${WORK_DIR}/sealed-${index}.key" -pubout | sha256sum | awk '{print $1}')"
  [[ "${cert_hash}" == "${key_hash}" ]] || fail "certificado e chave Sealed Secrets não correspondem"
done

kubectl delete namespace "${NAMESPACE}" --ignore-not-found --wait=true >/dev/null 2>&1 || true
kubectl create namespace "${NAMESPACE}"
repo_count="$(jq '.items | length' "${WORK_DIR}/argocd-repositories.json")"
(( repo_count >= 1 )) || fail "nenhum repositório Argo CD"
jq --arg ns "${NAMESPACE}" '{apiVersion:"v1",kind:"List",items:[.items[] | {apiVersion:"v1",kind:"Secret",metadata:{name:.metadata.name,namespace:$ns,labels:.metadata.labels},type:.type,data:.data}]}' "${WORK_DIR}/argocd-repositories.json" | kubectl apply -f - >/dev/null
for name in $(kubectl -n "${NAMESPACE}" get secret -l argocd.argoproj.io/secret-type=repository -o jsonpath='{.items[*].metadata.name}'); do
  type="$(kubectl -n "${NAMESPACE}" get secret "${name}" -o jsonpath='{.data.type}' | base64 -d)"
  url="$(kubectl -n "${NAMESPACE}" get secret "${name}" -o jsonpath='{.data.url}' | base64 -d)"
  [[ "${type}" == "git" && "${url}" == git@github.com:* ]] || fail "Secret Argo CD inválido: ${name}"
done

kubectl -n "${NAMESPACE}" apply -f - <<'EOF'
apiVersion: v1
kind: Pod
metadata:
  name: rabbitmq-restore
  labels: {app: rabbitmq-restore, team: platform, environment: lab}
spec:
  restartPolicy: Never
  containers:
    - name: rabbitmq
      image: docker.io/library/rabbitmq:3.13-management-alpine
      env:
        - {name: RABBITMQ_ERLANG_COOKIE, value: dr-validation-cookie}
EOF
kubectl -n "${NAMESPACE}" wait --for=condition=Ready pod/rabbitmq-restore --timeout=180s
for _ in $(seq 1 60); do
  kubectl -n "${NAMESPACE}" exec rabbitmq-restore -- rabbitmqctl await_startup >/dev/null 2>&1 && break
  sleep 2
done
kubectl -n "${NAMESPACE}" exec rabbitmq-restore -- rabbitmqctl await_startup >/dev/null
kubectl -n "${NAMESPACE}" cp "${WORK_DIR}/rabbitmq-definitions.json" rabbitmq-restore:/tmp/source-definitions.json
if ! jq -e '.users[] | select(.name == "guest")' "${WORK_DIR}/rabbitmq-definitions.json" >/dev/null; then
  kubectl -n "${NAMESPACE}" exec rabbitmq-restore -- rabbitmqctl delete_user guest >/dev/null
fi
kubectl -n "${NAMESPACE}" exec rabbitmq-restore -- rabbitmqctl import_definitions /tmp/source-definitions.json >/dev/null
kubectl -n "${NAMESPACE}" exec rabbitmq-restore -- rabbitmqctl export_definitions /tmp/restored-definitions.json >/dev/null
kubectl -n "${NAMESPACE}" exec rabbitmq-restore -- cat /tmp/restored-definitions.json >"${WORK_DIR}/restored-definitions.json"
canonical_filter='
  {
    users: ((.users // []) | sort_by(.name)),
    vhosts: ((.vhosts // []) | sort_by(.name)),
    permissions: ((.permissions // []) | sort_by(.vhost, .user)),
    topic_permissions: ((.topic_permissions // []) | sort_by(.vhost, .user, .exchange)),
    parameters: ((.parameters // []) | sort_by(.vhost, .component, .name)),
    global_parameters: ((.global_parameters // []) | sort_by(.name)),
    policies: ((.policies // []) | sort_by(.vhost, .name)),
    queues: ((.queues // []) | map(.arguments |= with_entries(select(.value != "undefined"))) | sort_by(.vhost, .name)),
    exchanges: ((.exchanges // []) | sort_by(.vhost, .name)),
    bindings: ((.bindings // []) | sort_by(.vhost, .source, .destination_type, .destination, .routing_key))
  }
'
jq -S "${canonical_filter}" "${WORK_DIR}/rabbitmq-definitions.json" >"${WORK_DIR}/source-canonical.json"
jq -S "${canonical_filter}" "${WORK_DIR}/restored-definitions.json" >"${WORK_DIR}/restored-canonical.json"
cmp -s "${WORK_DIR}/source-canonical.json" "${WORK_DIR}/restored-canonical.json" || fail "definitions RabbitMQ restauradas divergem"

echo "PLATFORM_RESTORE_STATUS=success"
echo "SEALED_SECRETS_KEYPAIRS=${key_count}"
echo "ARGOCD_REPOSITORIES=${repo_count}"
echo "RABBITMQ_USERS=$(jq '.users | length' "${WORK_DIR}/restored-definitions.json")"
echo "RABBITMQ_VHOSTS=$(jq '.vhosts | length' "${WORK_DIR}/restored-definitions.json")"
echo "RABBITMQ_QUEUES=$(jq '.queues | length' "${WORK_DIR}/restored-definitions.json")"
