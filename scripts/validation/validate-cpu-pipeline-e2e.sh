#!/usr/bin/env bash
set -euo pipefail

WORKERS_NS="${WORKERS_NS:-workers}"
MESSAGING_NS="${MESSAGING_NS:-messaging}"
RABBIT_POD="${RABBIT_POD:-rabbitmq-0}"
QUEUE_NAME="${QUEUE_NAME:-cpu-jobs}"
CPU_WORKER_SCALEDOBJECT="${CPU_WORKER_SCALEDOBJECT:-cpu-worker-rabbitmq}"

OBSERVE_SECONDS="${OBSERVE_SECONDS:-180}"
INTERVAL="${INTERVAL:-10}"
DRAIN_TIMEOUT="${DRAIN_TIMEOUT:-300}"

CPU_THRESHOLD_M="${CPU_THRESHOLD_M:-200}"

info() { printf '\n[INFO] %s\n' "$*"; }
pass() { printf '[ OK ] %s\n' "$*"; }
fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "Comando obrigatório não encontrado: $1"
}

cpu_to_millicores() {
  local v="$1"
  if [[ "$v" == *m ]]; then
    echo "${v%m}"
  else
    awk -v x="$v" 'BEGIN { printf "%.0f\n", x * 1000 }'
  fi
}

get_queue_metrics() {
  kubectl exec -n "${MESSAGING_NS}" "${RABBIT_POD}" -- \
    rabbitmqctl list_queues name messages_ready messages_unacknowledged consumers 2>/dev/null \
    | awk -v q="${QUEUE_NAME}" '$1==q {print $2 "|" $3 "|" $4}'
}

get_worker_ready_replicas() {
  local v
  v="$(kubectl get deploy cpu-worker -n "${WORKERS_NS}" -o jsonpath='{.status.readyReplicas}' 2>/dev/null || true)"
  echo "${v:-0}"
}

get_hpa_max_replicas() {
  kubectl get hpa "${CPU_WORKER_HPA}" -n "${WORKERS_NS}" -o jsonpath='{.spec.maxReplicas}'
}

get_hpa_desired_replicas() {
  local v
  v="$(kubectl get hpa "${CPU_WORKER_HPA}" -n "${WORKERS_NS}" -o jsonpath='{.status.desiredReplicas}' 2>/dev/null || true)"
  echo "${v:-0}"
}

get_hpa_condition_status() {
  local condition="$1"
  kubectl get hpa "${CPU_WORKER_HPA}" -n "${WORKERS_NS}" -o jsonpath="{range .status.conditions[?(@.type==\"${condition}\")]}{.status}{end}" 2>/dev/null || true
}

get_hpa_condition_reason() {
  local condition="$1"
  kubectl get hpa "${CPU_WORKER_HPA}" -n "${WORKERS_NS}" -o jsonpath="{range .status.conditions[?(@.type==\"${condition}\")]}{.reason}{end}" 2>/dev/null || true
}

get_max_worker_cpu_m() {
  local top_output
  top_output="$(kubectl top pods -n "${WORKERS_NS}" -l app=cpu-worker --no-headers 2>/dev/null || true)"

  if [[ -z "${top_output}" ]]; then
    echo "0"
    return
  fi

  awk '
    function cpu_to_m(v) {
      if (v ~ /m$/) {
        sub(/m$/, "", v)
        return v + 0
      }
      return (v + 0) * 1000
    }
    {
      m = cpu_to_m($2)
      if (m > max) max = m
    }
    END {
      if (max == "") max = 0
      printf "%.0f\n", max
    }
  ' <<< "${top_output}"
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

INITIAL_REPLICAS="$(get_worker_ready_replicas)"
CPU_WORKER_HPA="$(
  kubectl get scaledobject "${CPU_WORKER_SCALEDOBJECT}" -n "${WORKERS_NS}" \
    -o jsonpath='{.status.hpaName}'
)"
[[ -n "${CPU_WORKER_HPA}" ]] \
  || fail "ScaledObject/${CPU_WORKER_SCALEDOBJECT} ainda não publicou status.hpaName"
HPA_MAX_REPLICAS="$(get_hpa_max_replicas)"

[[ -n "${HPA_MAX_REPLICAS}" ]] || fail "Não foi possível descobrir spec.maxReplicas do HPA"

MAX_READY_MSGS=0
MAX_UNACK_MSGS=0
MAX_CONSUMERS=0
MAX_WORKER_REPLICAS="${INITIAL_REPLICAS}"
MAX_DESIRED_REPLICAS=0
MAX_WORKER_CPU_M=0

OBSERVED_BACKLOG="false"
OBSERVED_SCALING_ACTIVE="false"
OBSERVED_SCALING_LIMITED="false"

echo
info "Recriando o pod cpu-producer para disparar novo burst"
kubectl delete pod -n "${WORKERS_NS}" -l app=cpu-producer --wait=true
kubectl rollout status deployment/cpu-producer -n "${WORKERS_NS}" --timeout=180s
pass "Pod do cpu-producer recriado com sucesso"

echo
info "Observando por ${OBSERVE_SECONDS}s com intervalo de ${INTERVAL}s"

END_TS=$(( $(date +%s) + OBSERVE_SECONDS ))
SAMPLE=1

while [ "$(date +%s)" -lt "${END_TS}" ]; do
  echo
  echo "================ SAMPLE ${SAMPLE} - $(date '+%F %T') ================"

  QUEUE_METRICS="$(get_queue_metrics)"
  [[ -n "${QUEUE_METRICS}" ]] || fail "Não foi possível obter métricas da fila ${QUEUE_NAME}"

  IFS='|' read -r READY_MSGS UNACK_MSGS CONSUMERS <<< "${QUEUE_METRICS}"

  WORKER_REPLICAS="$(get_worker_ready_replicas)"
  DESIRED_REPLICAS="$(get_hpa_desired_replicas)"
  HPA_SCALING_ACTIVE="$(get_hpa_condition_status "ScalingActive")"
  HPA_SCALING_LIMITED="$(get_hpa_condition_status "ScalingLimited")"
  HPA_SCALING_LIMITED_REASON="$(get_hpa_condition_reason "ScalingLimited")"
  CURRENT_MAX_CPU_M="$(get_max_worker_cpu_m)"

  (( READY_MSGS > MAX_READY_MSGS )) && MAX_READY_MSGS="${READY_MSGS}"
  (( UNACK_MSGS > MAX_UNACK_MSGS )) && MAX_UNACK_MSGS="${UNACK_MSGS}"
  (( CONSUMERS > MAX_CONSUMERS )) && MAX_CONSUMERS="${CONSUMERS}"
  (( WORKER_REPLICAS > MAX_WORKER_REPLICAS )) && MAX_WORKER_REPLICAS="${WORKER_REPLICAS}"
  (( DESIRED_REPLICAS > MAX_DESIRED_REPLICAS )) && MAX_DESIRED_REPLICAS="${DESIRED_REPLICAS}"
  (( CURRENT_MAX_CPU_M > MAX_WORKER_CPU_M )) && MAX_WORKER_CPU_M="${CURRENT_MAX_CPU_M}"

  if (( READY_MSGS > 0 || UNACK_MSGS > 0 )); then
    OBSERVED_BACKLOG="true"
  fi

  if [[ "${HPA_SCALING_ACTIVE}" == "True" ]]; then
    OBSERVED_SCALING_ACTIVE="true"
  fi

  if [[ "${HPA_SCALING_LIMITED}" == "True" ]]; then
    OBSERVED_SCALING_LIMITED="true"
  fi

  echo "Fila: ready=${READY_MSGS} unacked=${UNACK_MSGS} consumers=${CONSUMERS}"
  echo "Workers: readyReplicas=${WORKER_REPLICAS}"
  echo "HPA: desired=${DESIRED_REPLICAS} scalingActive=${HPA_SCALING_ACTIVE:-<vazio>} scalingLimited=${HPA_SCALING_LIMITED:-<vazio>} reason=${HPA_SCALING_LIMITED_REASON:-<vazio>}"
  echo "Top CPU worker: maxPodCPU=${CURRENT_MAX_CPU_M}m"

  SAMPLE=$((SAMPLE + 1))
  sleep "${INTERVAL}"
done

info "Aguardando drenagem da fila por até ${DRAIN_TIMEOUT}s"
DRAIN_END_TS=$(( $(date +%s) + DRAIN_TIMEOUT ))
while true; do
  FINAL_QUEUE_METRICS="$(get_queue_metrics)"
  [[ -n "${FINAL_QUEUE_METRICS}" ]] || fail "Não foi possível obter métricas finais da fila ${QUEUE_NAME}"
  IFS='|' read -r FINAL_READY_MSGS FINAL_UNACK_MSGS _ <<< "${FINAL_QUEUE_METRICS}"
  echo "Fila durante drenagem: ready=${FINAL_READY_MSGS} unacked=${FINAL_UNACK_MSGS}"
  if (( FINAL_READY_MSGS == 0 && FINAL_UNACK_MSGS == 0 )); then
    break
  fi
  (( $(date +%s) < DRAIN_END_TS )) \
    || fail "Fila não drenou no timeout (ready=${FINAL_READY_MSGS}, unacked=${FINAL_UNACK_MSGS})"
  sleep "${INTERVAL}"
done

echo
info "Estado final"
kubectl get deploy,po,hpa -n "${WORKERS_NS}" -o wide
echo
kubectl describe hpa "${CPU_WORKER_HPA}" -n "${WORKERS_NS}" | sed -n '/Metrics:/,/Events:/p'
echo
kubectl exec -n "${MESSAGING_NS}" "${RABBIT_POD}" -- \
  rabbitmqctl list_queues name messages_ready messages_unacknowledged consumers

echo
info "Critérios de validação"

[[ "${OBSERVED_BACKLOG}" == "true" ]] \
  || fail "Não houve evidência de backlog na fila ${QUEUE_NAME}"

pass "Houve backlog na fila durante o teste (maxReady=${MAX_READY_MSGS}, maxUnacked=${MAX_UNACK_MSGS})"

(( MAX_CONSUMERS >= 1 )) \
  || fail "Não houve consumidores ativos na fila ${QUEUE_NAME}"

pass "Houve consumidores ativos (maxConsumers=${MAX_CONSUMERS})"

[[ "${OBSERVED_SCALING_ACTIVE}" == "true" ]] \
  || fail "O HPA não ficou ScalingActive=True durante o teste"

pass "HPA ficou ScalingActive=True"

if (( MAX_WORKER_CPU_M >= CPU_THRESHOLD_M )); then
  pass "Houve carga real de CPU nos workers (maxPodCPU=${MAX_WORKER_CPU_M}m)"
else
  fail "Carga de CPU insuficiente nos workers; maxPodCPU=${MAX_WORKER_CPU_M}m e threshold=${CPU_THRESHOLD_M}m"
fi

(( FINAL_READY_MSGS == 0 && FINAL_UNACK_MSGS == 0 )) \
  || fail "Fila não drenou ao final (ready=${FINAL_READY_MSGS}, unacked=${FINAL_UNACK_MSGS})"

pass "Fila drenou ao final"

if (( INITIAL_REPLICAS < HPA_MAX_REPLICAS )); then
  if (( MAX_WORKER_REPLICAS > INITIAL_REPLICAS || MAX_DESIRED_REPLICAS > INITIAL_REPLICAS )); then
    pass "Houve evidência de scale-up (initial=${INITIAL_REPLICAS}, maxReady=${MAX_WORKER_REPLICAS}, maxDesired=${MAX_DESIRED_REPLICAS}, hpaMax=${HPA_MAX_REPLICAS})"
  else
    fail "Não houve evidência de scale-up embora houvesse espaço para escalar (initial=${INITIAL_REPLICAS}, maxReady=${MAX_WORKER_REPLICAS}, maxDesired=${MAX_DESIRED_REPLICAS}, hpaMax=${HPA_MAX_REPLICAS})"
  fi
else
  if [[ "${OBSERVED_SCALING_LIMITED}" == "true" ]]; then
    pass "Teste começou no teto de réplicas e houve evidência de saturação no limite do HPA (initial=${INITIAL_REPLICAS}, hpaMax=${HPA_MAX_REPLICAS})"
  else
    pass "Teste começou no teto de réplicas; validamos backlog, consumo, CPU e drenagem sem exigir scale-up adicional"
  fi
fi

echo
info "Resumo final"
echo "initialReplicas=${INITIAL_REPLICAS}"
echo "hpaMaxReplicas=${HPA_MAX_REPLICAS}"
echo "maxReadyMessages=${MAX_READY_MSGS}"
echo "maxUnackedMessages=${MAX_UNACK_MSGS}"
echo "maxConsumers=${MAX_CONSUMERS}"
echo "maxWorkerReadyReplicas=${MAX_WORKER_REPLICAS}"
echo "maxDesiredReplicas=${MAX_DESIRED_REPLICAS}"
echo "maxWorkerCpu=${MAX_WORKER_CPU_M}m"
echo "finalReadyMessages=${FINAL_READY_MSGS}"
echo "finalUnackedMessages=${FINAL_UNACK_MSGS}"

pass "Validação E2E do pipeline CPU concluída com sucesso"
