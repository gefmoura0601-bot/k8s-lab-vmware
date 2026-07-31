#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# Descoberta de caminho do script
# ============================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MESH_SCRIPT="${SCRIPT_DIR}/validate-postgres-api-mesh.sh"

# ============================================================
# Configurações padrão do lab
# ============================================================
ARGOCD_NS="${ARGOCD_NS:-argocd}"
APPS_NS="${APPS_NS:-apps}"
WORKERS_NS="${WORKERS_NS:-workers}"
DATABASES_NS="${DATABASES_NS:-databases}"
MESSAGING_NS="${MESSAGING_NS:-messaging}"
NGINX_NS="${NGINX_NS:-nginx-lab}"

INGRESS_URL="${INGRESS_URL:-https://192.168.109.151:31882}"
HOST_HEADER="${HOST_HEADER:-nginx.lab.local}"
EXTERNAL_BASE_PATH="${EXTERNAL_BASE_PATH:-/postgres-api}"
GATEWAY_NAME="${GATEWAY_NAME:-nginx-lab-gateway}"
GATEWAY_NAMESPACE="${GATEWAY_NAMESPACE:-nginx-lab}"

POSTGRES_POD="${POSTGRES_POD:-postgres-0}"
POSTGRES_DB="${POSTGRES_DB:-appdb}"
POSTGRES_USER="${POSTGRES_USER:-appuser}"

RABBIT_POD="${RABBIT_POD:-rabbitmq-0}"
RABBIT_QUEUE="${RABBIT_QUEUE:-cpu-jobs}"
CPU_WORKER_SCALEDOBJECT="${CPU_WORKER_SCALEDOBJECT:-cpu-worker-rabbitmq}"

REQUIRED_APPS=(
  "postgres-api"
  "cpu-worker"
  "cpu-producer"
  "nginx-lab"
)

REQUIRED_POLICIES=(
  "allow-approved-registries"
  "disallow-latest-tag"
  "require-probes"
  "require-resources"
  "require-standard-labels"
)

# ============================================================
# Funções auxiliares
# ============================================================
info() { printf '\n[INFO] %s\n' "$*"; }
pass() { printf '[ OK ] %s\n' "$*"; }
fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "Comando obrigatório não encontrado: $1"
}

assert_app_synced_healthy() {
  local app="$1"
  local state

  state="$(kubectl get application "$app" -n "$ARGOCD_NS" -o jsonpath='{.status.sync.status}{"|"}{.status.health.status}')"
  [[ "$state" == "Synced|Healthy" ]] || fail "Application/${app} está em ${state}; esperado=Synced|Healthy"

  pass "Application/${app} = ${state}"
}

assert_clusterpolicy_ready() {
  local policy="$1"
  local ready
  local message

  ready="$(kubectl get cpol "$policy" -o jsonpath='{range .status.conditions[?(@.type=="Ready")]}{.status}{end}')"
  message="$(kubectl get cpol "$policy" -o jsonpath='{range .status.conditions[?(@.type=="Ready")]}{.message}{end}')"

  [[ "$ready" == "True" || "$ready" == "true" ]]     || fail "ClusterPolicy/${policy} não está Ready (status=${ready:-<vazio>}, message=${message:-<vazia>})"

  pass "ClusterPolicy/${policy} está Ready"
}

# ============================================================
# Pré-checagens
# ============================================================
info "Pré-checagens"
require_cmd bash
require_cmd kubectl
require_cmd curl
require_cmd awk
require_cmd grep
require_cmd sed

[[ -f "$MESH_SCRIPT" ]] || fail "Script especializado não encontrado: $MESH_SCRIPT"
pass "Dependências e script especializado encontrados"

# ============================================================
# 1) Nodes prontos
# ============================================================
info "1) Validando nodes"

kubectl get nodes -o wide

NOT_READY_NODES="$(
  kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}{"|"}{range .status.conditions[?(@.type=="Ready")]}{.status}{end}{"\n"}{end}' \
  | awk -F'|' '$2 != "True" {print $1}'
)"

[[ -z "${NOT_READY_NODES}" ]] || fail "Há nodes não prontos: ${NOT_READY_NODES}"
pass "Todos os nodes estão Ready"

# ============================================================
# 2) ArgoCD Applications principais
# ============================================================
info "2) Validando Applications do ArgoCD"

for app in "${REQUIRED_APPS[@]}"; do
  assert_app_synced_healthy "$app"
done

# ============================================================
# 3) Rollouts principais
# ============================================================
info "3) Validando rollouts principais"

kubectl rollout status deployment/postgres-api -n "$APPS_NS" --timeout=180s >/dev/null
pass "Deployment/postgres-api disponível"

kubectl rollout status deployment/cpu-worker -n "$WORKERS_NS" --timeout=180s >/dev/null
pass "Deployment/cpu-worker disponível"

kubectl rollout status deployment/cpu-producer -n "$WORKERS_NS" --timeout=180s >/dev/null
pass "Deployment/cpu-producer disponível"

kubectl rollout status deployment/nginx-lab -n "$NGINX_NS" --timeout=180s >/dev/null
pass "Deployment/nginx-lab disponível"

kubectl rollout status statefulset/postgres -n "$DATABASES_NS" --timeout=180s >/dev/null
pass "StatefulSet/postgres disponível"

kubectl rollout status statefulset/rabbitmq -n "$MESSAGING_NS" --timeout=180s >/dev/null
pass "StatefulSet/rabbitmq disponível"

# ============================================================
# 4) Validação especializada do mesh da postgres-api
# ============================================================
info "4) Executando validação especializada do mesh da postgres-api"

APP_NAME="postgres-api" \
APP_NAMESPACE="$APPS_NS" \
APP_SELECTOR="app=postgres-api" \
SERVICE_PORT="80" \
INTERNAL_HEALTH_PATH="/healthz" \
EXTERNAL_BASE_PATH="$EXTERNAL_BASE_PATH" \
GATEWAY_NAME="$GATEWAY_NAME" \
GATEWAY_NAMESPACE="$GATEWAY_NAMESPACE" \
VIRTUALSERVICE_NAME="postgres-api" \
DESTINATIONRULE_NAME="postgres-api" \
AUTHZPOLICY_NAME="postgres-api" \
VALIDATION_NAMESPACE="no-mesh-test" \
NEGATIVE_POD_NAME="curl-negative" \
NEGATIVE_IMAGE="curlimages/curl:8.10.1" \
INGRESS_URL="$INGRESS_URL" \
HOST_HEADER="$HOST_HEADER" \
bash "$MESH_SCRIPT"

pass "Validação especializada do mesh concluída"

# ============================================================
# 5) PostgreSQL
# ============================================================
info "5) Validando PostgreSQL"

kubectl exec -n "$DATABASES_NS" "$POSTGRES_POD" -- \
  pg_isready -h 127.0.0.1 -p 5432 -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null

pass "PostgreSQL está respondendo"

# ============================================================
# 6) RabbitMQ
# ============================================================
info "6) Validando RabbitMQ"

RABBIT_OUTPUT="$(kubectl exec -n "$MESSAGING_NS" "$RABBIT_POD" -- rabbitmqctl list_queues name messages consumers)"
printf '%s\n' "$RABBIT_OUTPUT"

printf '%s\n' "$RABBIT_OUTPUT" | grep -q "^${RABBIT_QUEUE}[[:space:]]" \
  || fail "Fila ${RABBIT_QUEUE} não encontrada no RabbitMQ"

pass "Fila ${RABBIT_QUEUE} encontrada no RabbitMQ"

# ============================================================
# 7) KEDA e HPA gerado
# ============================================================
info "7) Validando autoscaling KEDA do cpu-worker"

kubectl get scaledobject "$CPU_WORKER_SCALEDOBJECT" -n "$WORKERS_NS" >/dev/null \
  || fail "ScaledObject/${CPU_WORKER_SCALEDOBJECT} não encontrado no namespace ${WORKERS_NS}"

CPU_WORKER_HPA="$(
  kubectl get scaledobject "$CPU_WORKER_SCALEDOBJECT" -n "$WORKERS_NS" \
    -o jsonpath='{.status.hpaName}'
)"

[[ -n "$CPU_WORKER_HPA" ]] \
  || fail "ScaledObject/${CPU_WORKER_SCALEDOBJECT} ainda não publicou status.hpaName"

kubectl get hpa "$CPU_WORKER_HPA" -n "$WORKERS_NS"
pass "ScaledObject/${CPU_WORKER_SCALEDOBJECT} e HPA/${CPU_WORKER_HPA} encontrados"

# ============================================================
# 8) Kyverno
# ============================================================
info "8) Validando ClusterPolicies do Kyverno"

for policy in "${REQUIRED_POLICIES[@]}"; do
  assert_clusterpolicy_ready "$policy"
done

# ============================================================
# Resumo final
# ============================================================
info "Resumo final"
pass "Smoke test da plataforma concluído com sucesso"

echo
echo "Resumo:"
echo "- nodes Ready"
echo "- Applications principais Synced/Healthy"
echo "- rollouts principais OK"
echo "- mesh/mTLS da postgres-api OK"
echo "- PostgreSQL respondendo"
echo "- RabbitMQ com fila ${RABBIT_QUEUE}"
echo "- autoscaling KEDA do cpu-worker presente"
echo "- ClusterPolicies do Kyverno Ready"
