#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-memory-lab}"
DEPLOYMENT="${DEPLOYMENT:-memory-worker}"
SCALEDOBJECT="${SCALEDOBJECT:-memory-worker-rabbitmq}"
QUEUE="${QUEUE:-memory-jobs}"
TIMEOUT="${TIMEOUT:-240}"

fail() { echo "ERRO: $*" >&2; exit 1; }
replicas() { kubectl -n "${NAMESPACE}" get deploy "${DEPLOYMENT}" -o jsonpath='{.spec.replicas}'; }
queue_messages() {
  # As variáveis são expandidas pelo shell dentro do pod RabbitMQ.
  # shellcheck disable=SC2016
  kubectl -n messaging exec rabbitmq-0 -- sh -c \
    'rabbitmqadmin -u "$RABBITMQ_DEFAULT_USER" -p "$RABBITMQ_DEFAULT_PASS" -V / list queues name messages --format=tsv' 2>/dev/null |
    awk -v queue="${QUEUE}" '$1 == queue {print $2}'
}
rabbit_admin() {
  # As variáveis são expandidas pelo shell dentro do pod RabbitMQ.
  # shellcheck disable=SC2016
  kubectl -n messaging exec rabbitmq-0 -- sh -c \
    'rabbitmqadmin -u "$RABBITMQ_DEFAULT_USER" -p "$RABBITMQ_DEFAULT_PASS" -V / '"$*"
}
publish() { rabbit_admin publish exchange=amq.default routing_key="${QUEUE}" payload="$1" >/dev/null; }

rabbit_admin delete queue name="${QUEUE}" >/dev/null 2>&1 || true
rabbit_admin declare queue name="${QUEUE}" durable=true >/dev/null
kubectl -n "${NAMESPACE}" wait --for=condition=Ready "scaledobject/${SCALEDOBJECT}" --timeout=120s
[[ "$(replicas)" == "0" ]] || fail "estado inicial não está em zero"
for payload in 16 160 16 16 16 16 16 16 16 16 16 16; do publish "${payload}"; done

echo "Carga publicada: 11 normais e um OOM controlado"
deadline=$((SECONDS + TIMEOUT))
max_replicas=0
oom_seen=false
while (( SECONDS < deadline )); do
  current="$(replicas)"
  messages="$(queue_messages)"; messages="${messages:-0}"
  restarts="$(kubectl -n "${NAMESPACE}" get pods -l app=memory-worker -o json | jq '[.items[].status.containerStatuses[]?.restartCount] | add // 0')"
  if (( current > max_replicas )); then max_replicas="${current}"; fi
  if kubectl -n "${NAMESPACE}" get pods -l app=memory-worker -o json | jq -e '.items[].status.containerStatuses[]? | select(.lastState.terminated.reason == "OOMKilled" or .state.terminated.reason == "OOMKilled")' >/dev/null; then
    oom_seen=true
  fi
  printf 'replicas=%s queue=%s restarts=%s oom=%s\n' "${current}" "${messages}" "${restarts}" "${oom_seen}"
  if [[ "${messages}" == "0" && "${oom_seen}" == true ]]; then break; fi
  sleep 2
done

[[ "${oom_seen}" == true ]] || fail "OOMKilled controlado não foi observado"
[[ "$(queue_messages)" == "0" ]] || fail "fila não foi drenada"
(( max_replicas == 2 )) || fail "KEDA não atingiu o limite seguro de 2 réplicas (máximo=${max_replicas})"

kubectl -n "${NAMESPACE}" wait --for=jsonpath='{.spec.replicas}'=0 "deployment/${DEPLOYMENT}" --timeout=120s
pressure="$(kubectl get nodes -o json | jq '[.items[].status.conditions[] | select(.type=="MemoryPressure" and .status!="False")] | length')"
[[ "${pressure}" == "0" ]] || fail "MemoryPressure foi detectado em algum nó"

echo "OK: scale 0->2->0, OOMKilled observado, fila drenada e nodes sem MemoryPressure"
