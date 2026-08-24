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
ISTIO_INGRESS_NAMESPACE="${ISTIO_INGRESS_NAMESPACE:-istio-system}"

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
  "nginx-lab-tls"
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

wait_for_rollout_healthy() {
  local rollout="$1"
  local namespace="$2"
  local phase

  for _ in $(seq 1 90); do
    phase="$(kubectl get rollout "$rollout" -n "$namespace" -o jsonpath='{.status.phase}' 2>/dev/null || true)"
    [[ "$phase" == "Healthy" ]] && break
    sleep 2
  done

  [[ "$phase" == "Healthy" ]] || fail "Rollout/${rollout} não ficou Healthy no namespace ${namespace} (fase=${phase:-vazia})"
  pass "Rollout/${rollout} está Healthy"
}

assert_nginx_https() {
  local authority ingress_ip ingress_port http_code tls_mode credential_name

  authority="$(printf '%s' "$INGRESS_URL" | sed -E 's#^https://([^/]+).*$#\1#')"
  [[ "$authority" != "$INGRESS_URL" ]] || fail "INGRESS_URL deve usar HTTPS (recebido: ${INGRESS_URL})"
  ingress_ip="${authority%%:*}"
  ingress_port="${authority##*:}"
  [[ "$ingress_ip" != "$ingress_port" ]] || ingress_port="443"

  tls_mode="$(kubectl get gateway "$GATEWAY_NAME" -n "$GATEWAY_NAMESPACE" -o jsonpath='{.spec.servers[?(@.port.number==443)].tls.mode}')"
  credential_name="$(kubectl get gateway "$GATEWAY_NAME" -n "$GATEWAY_NAMESPACE" -o jsonpath='{.spec.servers[?(@.port.number==443)].tls.credentialName}')"
  [[ "$tls_mode" == "SIMPLE" && -n "$credential_name" ]] || fail "Gateway/${GATEWAY_NAME} não possui TLS SIMPLE com credentialName na porta 443"
  kubectl get secret "$credential_name" -n "$ISTIO_INGRESS_NAMESPACE" >/dev/null || fail "Secret TLS/${credential_name} não encontrado no namespace ${ISTIO_INGRESS_NAMESPACE}"

  http_code="$(curl -sk --noproxy '*' --resolve "${HOST_HEADER}:${ingress_port}:${ingress_ip}" -o /dev/null -w '%{http_code}' --max-time 10 "https://${HOST_HEADER}:${ingress_port}/")"
  [[ "$http_code" == "200" ]] || fail "HTTPS do nginx-lab retornou HTTP ${http_code}; esperado=200"
  pass "Gateway TLS e HTTPS do nginx-lab estão funcionando"
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
# 2) Calico
# ============================================================
info "2) Validando Calico"

CALICO_AVAILABLE="$(
  kubectl get tigerastatus calico \
    -o jsonpath='{range .status.conditions[?(@.type=="Available")]}{.status}{end}'
)"
[[ "${CALICO_AVAILABLE}" == "True" ]] \
  || fail "TigerStatus/calico não está Available (status=${CALICO_AVAILABLE:-<vazio>})"

kubectl rollout status daemonset/calico-node -n calico-system --timeout=180s >/dev/null
pass "Calico disponível em todos os nodes"

# ============================================================
# 3) ArgoCD Applications principais
# ============================================================
info "3) Validando Applications do ArgoCD"

for app in "${REQUIRED_APPS[@]}"; do
  assert_app_synced_healthy "$app"
done

# ============================================================
# 4) HTTPS do nginx-lab via Gateway Istio
# ============================================================
info "4) Validando HTTPS do nginx-lab"
assert_nginx_https

# ============================================================
# 5) Workloads principais
# ============================================================
info "5) Validando workloads principais"

wait_for_rollout_healthy "postgres-api" "$APPS_NS"

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
# 5) Validação especializada do mesh da postgres-api
# ============================================================
info "5) Executando validação especializada do mesh da postgres-api"

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
# 6) PostgreSQL
# ============================================================
info "6) Validando PostgreSQL"

kubectl exec -n "$DATABASES_NS" "$POSTGRES_POD" -- \
  pg_isready -h 127.0.0.1 -p 5432 -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null

pass "PostgreSQL está respondendo"

# ============================================================
# 7) RabbitMQ
# ============================================================
info "7) Validando RabbitMQ"

RABBIT_OUTPUT="$(kubectl exec -n "$MESSAGING_NS" "$RABBIT_POD" -- \
  rabbitmqctl list_queues name type state messages consumers --local --timeout 30 2>&1)"
printf '%s\n' "$RABBIT_OUTPUT"

printf '%s\n' "$RABBIT_OUTPUT" | grep -qE 'badrpc|unresponsive' \
  && fail "RabbitMQ reportou fila não responsiva"

printf '%s\n' "$RABBIT_OUTPUT" | grep -q "^${RABBIT_QUEUE}[[:space:]]" \
  || fail "Fila ${RABBIT_QUEUE} não encontrada no RabbitMQ"

RABBIT_QUORUM_OUTPUT="$(kubectl exec -n "$MESSAGING_NS" "$RABBIT_POD" -- \
  rabbitmq-queues quorum_status "$RABBIT_QUEUE")"
printf '%s\n' "$RABBIT_QUORUM_OUTPUT"

RABBIT_VOTERS="$(printf '%s\n' "$RABBIT_QUORUM_OUTPUT" | grep -c 'voter' || true)"
[[ "$RABBIT_VOTERS" -eq 3 ]] \
  || fail "Fila ${RABBIT_QUEUE} possui ${RABBIT_VOTERS} membros votantes; esperado: 3"

pass "Fila quorum ${RABBIT_QUEUE} disponível com 3 membros votantes"

# ============================================================
# 8) KEDA e HPA gerado
# ============================================================
info "8) Validando autoscaling KEDA do cpu-worker"

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
# 9) Kyverno
# ============================================================
info "9) Validando ClusterPolicies do Kyverno"

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
echo "- Calico disponível"
echo "- Applications principais Synced/Healthy"
echo "- Gateway TLS e HTTPS do nginx-lab OK"
echo "- workloads principais OK"
echo "- mesh/mTLS da postgres-api OK"
echo "- PostgreSQL respondendo"
echo "- RabbitMQ com fila ${RABBIT_QUEUE}"
echo "- autoscaling KEDA do cpu-worker presente"
echo "- ClusterPolicies do Kyverno Ready"
