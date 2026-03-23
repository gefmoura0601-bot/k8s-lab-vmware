#!/usr/bin/env bash
set -euo pipefail

WORKERS_NS="${WORKERS_NS:-workers}"
MESSAGING_NS="${MESSAGING_NS:-messaging}"
RABBIT_POD="${RABBIT_POD:-rabbitmq-0}"
QUEUE_NAME="${QUEUE_NAME:-cpu-jobs}"
OBSERVE_SECONDS="${OBSERVE_SECONDS:-180}"
INTERVAL="${INTERVAL:-10}"

info() { printf '\n[INFO] %s\n' "$*"; }
pass() { printf '[ OK ] %s\n' "$*"; }
fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "Comando obrigatório não encontrado: $1"
}

require_cmd kubectl
require_cmd awk
require_cmd grep
require_cmd date

info "Estado inicial"
kubectl get deploy,po,hpa -n "${WORKERS_NS}" -o wide
echo
kubectl exec -n "${MESSAGING_NS}" "${RABBIT_POD}" -- \
  rabbitmqctl list_queues name messages_ready messages_unacknowledged consumers

echo
info "Reiniciando cpu-producer para disparar novo burst"
kubectl rollout restart deployment/cpu-producer -n "${WORKERS_NS}"
kubectl rollout status deployment/cpu-producer -n "${WORKERS_NS}" --timeout=180s
pass "cpu-producer reiniciado com sucesso"

echo
info "Observando pipeline por ${OBSERVE_SECONDS}s (intervalo ${INTERVAL}s)"

end_ts=$(( $(date +%s) + OBSERVE_SECONDS ))
sample=1

while [ "$(date +%s)" -lt "${end_ts}" ]; do
  echo
  echo "================ SAMPLE ${sample} - $(date '+%F %T') ================"

  echo
  echo "--- HPA cpu-worker ---"
  kubectl get hpa cpu-worker -n "${WORKERS_NS}"

  echo
  echo "--- Pods cpu-worker ---"
  kubectl get pods -n "${WORKERS_NS}" -l app=cpu-worker -o wide

  echo
  echo "--- Top pods workers ---"
  kubectl top pods -n "${WORKERS_NS}" 2>/dev/null || echo "kubectl top indisponível neste instante"

  echo
  echo "--- RabbitMQ queue ${QUEUE_NAME} ---"
  kubectl exec -n "${MESSAGING_NS}" "${RABBIT_POD}" -- \
    rabbitmqctl list_queues name messages_ready messages_unacknowledged consumers \
    | grep -E "^name|^${QUEUE_NAME}[[:space:]]" || true

  echo
  echo "--- Últimos logs cpu-worker ---"
  kubectl logs -n "${WORKERS_NS}" deploy/cpu-worker --tail=8 || true

  sample=$((sample + 1))
  sleep "${INTERVAL}"
done

echo
info "Estado final"
kubectl get deploy,po,hpa -n "${WORKERS_NS}" -o wide
echo
kubectl describe hpa cpu-worker -n "${WORKERS_NS}" | sed -n '/Metrics:/,/Events:/p'
echo
kubectl exec -n "${MESSAGING_NS}" "${RABBIT_POD}" -- \
  rabbitmqctl list_queues name messages_ready messages_unacknowledged consumers

pass "Observação E2E concluída"
