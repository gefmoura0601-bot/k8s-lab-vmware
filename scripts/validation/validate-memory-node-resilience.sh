#!/usr/bin/env bash
set -Eeuo pipefail

NAMESPACE="${NAMESPACE:-memory-lab}"
DEPLOYMENT="${DEPLOYMENT:-memory-worker}"
SCALEDOBJECT="${SCALEDOBJECT:-memory-worker-rabbitmq}"
QUEUE="${QUEUE:-memory-jobs}"
TARGET_NODE="${TARGET_NODE:-k8s-worker-01}"
case "${TARGET_NODE}" in
  k8s-worker-01) OTHER_NODE="k8s-worker-02" ;;
  k8s-worker-02) OTHER_NODE="k8s-worker-01" ;;
  *) echo "ERRO: TARGET_NODE deve ser k8s-worker-01 ou k8s-worker-02" >&2; exit 1 ;;
esac
KEY_SOURCE="${KEY_SOURCE:-/workspace/iac/vagrant/.vagrant/machines/${TARGET_NODE}/vmware_desktop/private_key}"
KEY_FILE="$(mktemp)"
NODE_RECOVERY_REQUIRED=false

fail() { echo "ERRO: $*" >&2; exit 1; }
replicas() { kubectl -n "${NAMESPACE}" get deploy "${DEPLOYMENT}" -o jsonpath='{.spec.replicas}'; }
# As variáveis são expandidas pelo shell dentro do pod RabbitMQ.
# shellcheck disable=SC2016
rabbit_admin() { kubectl -n messaging exec rabbitmq-0 -- sh -c 'rabbitmqadmin -u "$RABBITMQ_DEFAULT_USER" -p "$RABBITMQ_DEFAULT_PASS" -V / '"$*"; }
queue_messages() {
  # As variáveis são expandidas pelo shell dentro do pod RabbitMQ.
  # shellcheck disable=SC2016
  kubectl -n messaging exec rabbitmq-0 -- sh -c 'rabbitmqadmin -u "$RABBITMQ_DEFAULT_USER" -p "$RABBITMQ_DEFAULT_PASS" -V / list queues name messages --format=tsv' 2>/dev/null |
    awk -v queue="${QUEUE}" '$1 == queue {print $2}'
}
remote() { ssh -i "${KEY_FILE}" -o IdentitiesOnly=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "vagrant@${TARGET_NODE}" "$@"; }
recover_node() {
  exit_status=$?
  if (( exit_status != 0 )); then
    rabbit_admin delete queue name="${QUEUE}" >/dev/null 2>&1 || true
    rabbit_admin declare queue name="${QUEUE}" durable=true >/dev/null 2>&1 || true
  fi
  if [[ "${NODE_RECOVERY_REQUIRED}" == true ]]; then
    remote 'sudo systemctl start containerd; sudo systemctl start kubelet' || true
    kubectl wait --for=condition=Ready "node/${TARGET_NODE}" --timeout=180s || true
    kubectl uncordon "${TARGET_NODE}" || true
  fi
  rm -f "${KEY_FILE}"
}
trap recover_node EXIT

[[ -r "${KEY_SOURCE}" ]] || fail "chave SSH do ${TARGET_NODE} não encontrada em ${KEY_SOURCE}"
install -m 0600 "${KEY_SOURCE}" "${KEY_FILE}"
[[ "$(replicas)" == "0" ]] || fail "memory-worker deve iniciar em zero"
rabbit_admin delete queue name="${QUEUE}" >/dev/null 2>&1 || true
rabbit_admin declare queue name="${QUEUE}" durable=true >/dev/null
kubectl -n "${NAMESPACE}" wait --for=condition=Ready "scaledobject/${SCALEDOBJECT}" --timeout=120s
for _ in $(seq 1 30); do rabbit_admin publish exchange=amq.default routing_key="${QUEUE}" payload=16 >/dev/null; done

kubectl -n "${NAMESPACE}" rollout status "deployment/${DEPLOYMENT}" --timeout=120s
for _ in $(seq 1 60); do
  [[ "$(replicas)" == "2" ]] && [[ "$(kubectl -n "${NAMESPACE}" get deploy "${DEPLOYMENT}" -o jsonpath='{.status.readyReplicas}')" == "2" ]] && break
  sleep 2
done
[[ "$(replicas)" == "2" ]] || fail "KEDA não escalou para duas réplicas"
TARGET_POD="$(kubectl -n "${NAMESPACE}" get pods -l app=memory-worker -o json | jq -r --arg node "${TARGET_NODE}" '.items[] | select(.spec.nodeName==$node) | .metadata.name' | head -1)"
[[ -n "${TARGET_POD}" ]] || fail "nenhum memory-worker foi distribuído no ${TARGET_NODE}"

echo "Falha controlada: pod=${TARGET_POD} node=${TARGET_NODE}"
kubectl cordon "${TARGET_NODE}"
NODE_RECOVERY_REQUIRED=true
remote 'sudo systemctl stop kubelet; sudo systemctl stop containerd'
for _ in $(seq 1 60); do
  ready_status="$(kubectl get node "${TARGET_NODE}" -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}')"
  [[ "${ready_status}" != "True" ]] && break
  sleep 2
done
[[ "${ready_status}" != "True" ]] || fail "${TARGET_NODE} não ficou NotReady"
kubectl -n "${NAMESPACE}" delete pod "${TARGET_POD}" --force --grace-period=0

REPLACEMENT=""
for _ in $(seq 1 90); do
  REPLACEMENT="$(kubectl -n "${NAMESPACE}" get pods -l app=memory-worker -o json | jq -r --arg old "${TARGET_POD}" --arg node "${OTHER_NODE}" '.items[] | select(.metadata.name!=$old and .spec.nodeName==$node and .status.containerStatuses[0].ready==true) | .metadata.name' | head -1)"
  [[ -n "${REPLACEMENT}" ]] && break
  sleep 2
done
[[ -n "${REPLACEMENT}" ]] || fail "pod substituto não ficou Ready no ${OTHER_NODE}"
echo "Reagendamento confirmado: ${TARGET_POD} -> ${REPLACEMENT} em ${OTHER_NODE}"

remote 'sudo systemctl start containerd; sudo systemctl start kubelet'
kubectl wait --for=condition=Ready "node/${TARGET_NODE}" --timeout=180s
kubectl uncordon "${TARGET_NODE}"
NODE_RECOVERY_REQUIRED=false

for _ in $(seq 1 120); do
  messages="$(queue_messages)"; messages="${messages:-0}"
  [[ "${messages}" == "0" ]] && break
  sleep 2
done
[[ "$(queue_messages)" == "0" ]] || fail "fila não foi drenada após o reagendamento"
kubectl -n "${NAMESPACE}" wait --for=jsonpath='{.spec.replicas}'=0 "deployment/${DEPLOYMENT}" --timeout=180s
pressure="$(kubectl get nodes -o json | jq '[.items[].status.conditions[] | select(.type=="MemoryPressure" and .status!="False")] | length')"
[[ "${pressure}" == "0" ]] || fail "MemoryPressure detectado"
echo "OK: falha de ${TARGET_NODE}, reagendamento, drenagem, recuperação e scale-down validados"
