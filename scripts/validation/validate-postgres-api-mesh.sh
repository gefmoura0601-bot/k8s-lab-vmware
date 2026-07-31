#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# Configurações padrão
# ============================================================
APP_NAME="${APP_NAME:-postgres-api}"
APP_NAMESPACE="${APP_NAMESPACE:-apps}"
APP_SELECTOR="${APP_SELECTOR:-app=postgres-api}"

# Porta do Service interno da aplicação
SERVICE_PORT="${SERVICE_PORT:-80}"

# Path interno real da aplicação
INTERNAL_HEALTH_PATH="${INTERNAL_HEALTH_PATH:-/healthz}"

# Path externo publicado no Gateway/VirtualService
# Ex.: /postgres-api
EXTERNAL_BASE_PATH="${EXTERNAL_BASE_PATH:-}"

GATEWAY_NAME="${GATEWAY_NAME:-postgres-api}"
GATEWAY_NAMESPACE="${GATEWAY_NAMESPACE:-$APP_NAMESPACE}"
VIRTUALSERVICE_NAME="${VIRTUALSERVICE_NAME:-postgres-api}"
DESTINATIONRULE_NAME="${DESTINATIONRULE_NAME:-postgres-api}"
AUTHZPOLICY_NAME="${AUTHZPOLICY_NAME:-postgres-api}"

VALIDATION_NAMESPACE="${VALIDATION_NAMESPACE:-no-mesh-test}"
NEGATIVE_POD_NAME="${NEGATIVE_POD_NAME:-curl-negative}"
NEGATIVE_IMAGE="${NEGATIVE_IMAGE:-curlimages/curl:8.10.1}"

INGRESS_URL="${INGRESS_URL:-}"
HOST_HEADER="${HOST_HEADER:-}"

DIRECT_URL="http://${APP_NAME}.${APP_NAMESPACE}.svc.cluster.local:${SERVICE_PORT}${INTERNAL_HEALTH_PATH}"

# ============================================================
# Funções auxiliares
# ============================================================
info() { printf '\n[INFO] %s\n' "$*"; }
pass() { printf '[ OK ] %s\n' "$*"; }
fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }

cleanup() {
  kubectl delete pod "${NEGATIVE_POD_NAME}" -n "${VALIDATION_NAMESPACE}" --ignore-not-found >/dev/null 2>&1 || true
}
trap cleanup EXIT

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "Comando obrigatório não encontrado: $1"
}

check_exists() {
  local kind="$1"
  local name="$2"
  local namespace="$3"

  kubectl get "${kind}" "${name}" -n "${namespace}" >/dev/null 2>&1 \
    || fail "${kind}/${name} não encontrado no namespace ${namespace}"

  pass "${kind}/${name} encontrado em ${namespace}"
}

# ============================================================
# Pré-checagens
# ============================================================
[[ -n "${INGRESS_URL}" ]] || fail "Defina INGRESS_URL. Ex.: https://192.168.109.151:31882"
[[ -n "${HOST_HEADER}" ]] || fail "Defina HOST_HEADER. Ex.: nginx.lab.local"

require_cmd kubectl
require_cmd curl
require_cmd awk
require_cmd grep
require_cmd sed

# ============================================================
# 1) Aplicação disponível
# ============================================================
info "1) Validando rollout da aplicação"
kubectl rollout status deployment/"${APP_NAME}" -n "${APP_NAMESPACE}" --timeout=120s >/dev/null
pass "Deployment/${APP_NAME} está disponível"

# ============================================================
# 2) Sidecar Istio presente nos pods
# ============================================================
info "2) Validando injeção do sidecar Istio"

PODS="$(kubectl get pods -n "${APP_NAMESPACE}" -l "${APP_SELECTOR}" -o jsonpath='{range .items[*]}{.metadata.name}{"|"}{range .spec.initContainers[*]}{.name}{","}{end}{range .spec.containers[*]}{.name}{","}{end}{"\n"}{end}')"
[[ -n "${PODS}" ]] || fail "Nenhum pod encontrado com selector ${APP_SELECTOR} no namespace ${APP_NAMESPACE}"

MISSING_SIDECAR="$(printf '%s\n' "${PODS}" | awk -F'|' '$2 !~ /istio-proxy/ {print $1}')"
[[ -z "${MISSING_SIDECAR}" ]] || fail "Pods sem sidecar Istio: ${MISSING_SIDECAR}"

pass "Todos os pods da aplicação têm o sidecar istio-proxy"

# ============================================================
# 3) Objetos esperados do mesh
# ============================================================
info "3) Validando objetos do mesh"
check_exists gateway "${GATEWAY_NAME}" "${GATEWAY_NAMESPACE}"
check_exists virtualservice "${VIRTUALSERVICE_NAME}" "${APP_NAMESPACE}"
check_exists destinationrule "${DESTINATIONRULE_NAME}" "${APP_NAMESPACE}"
check_exists authorizationpolicy "${AUTHZPOLICY_NAME}" "${APP_NAMESPACE}"

# ============================================================
# 4) mTLS esperado
# ============================================================
info "4) Validando mTLS esperado"

TLS_MODE="$(kubectl get destinationrule "${DESTINATIONRULE_NAME}" -n "${APP_NAMESPACE}" -o jsonpath='{.spec.trafficPolicy.tls.mode}')"
[[ "${TLS_MODE}" == "ISTIO_MUTUAL" ]] || fail "DestinationRule/${DESTINATIONRULE_NAME} com tls.mode=${TLS_MODE}; esperado=ISTIO_MUTUAL"
pass "DestinationRule com ISTIO_MUTUAL"

PEER_STRICT_OUTPUT="$(kubectl get peerauthentication -A -o jsonpath='{range .items[*]}{.metadata.namespace}{"/"}{.metadata.name}{"="}{.spec.mtls.mode}{"\n"}{end}')"

printf '%s\n' "${PEER_STRICT_OUTPUT}" | grep -Eq "^${APP_NAMESPACE}/.*=STRICT$|^istio-system/default=STRICT$" \
  || fail "Nenhuma PeerAuthentication STRICT encontrada para o namespace ${APP_NAMESPACE} (nem default mesh-wide)"

pass "PeerAuthentication STRICT encontrada"

# ============================================================
# 5) Teste positivo via ingress
# ============================================================
info "5) Teste positivo: acesso via Ingress/Gateway deve retornar 200"

if [[ -n "${EXTERNAL_BASE_PATH}" ]]; then
  POSITIVE_PATH="${EXTERNAL_BASE_PATH%/}${INTERNAL_HEALTH_PATH}"
else
  POSITIVE_PATH="${INTERNAL_HEALTH_PATH}"
fi

INGRESS_SCHEME="$(printf '%s' "${INGRESS_URL}" | sed -E 's#^(https?)://.*#\1#')"
INGRESS_AUTHORITY="$(printf '%s' "${INGRESS_URL}" | sed -E 's#^https?://([^/]+).*$#\1#')"

INGRESS_IP="${INGRESS_AUTHORITY%%:*}"
INGRESS_PORT="${INGRESS_AUTHORITY##*:}"

if [[ "${INGRESS_IP}" == "${INGRESS_PORT}" ]]; then
  if [[ "${INGRESS_SCHEME}" == "https" ]]; then
    INGRESS_PORT="443"
  else
    INGRESS_PORT="80"
  fi
fi

if [[ "${INGRESS_SCHEME}" == "https" ]]; then
  HTTP_CODE="$(
    curl -sk --noproxy '*' \
      --resolve "${HOST_HEADER}:${INGRESS_PORT}:${INGRESS_IP}" \
      -o /dev/null \
      -w '%{http_code}' \
      --max-time 10 \
      "${INGRESS_SCHEME}://${HOST_HEADER}:${INGRESS_PORT}${POSITIVE_PATH}"
  )"
else
  HTTP_CODE="$(
    curl -s --noproxy '*' \
      -o /dev/null \
      -w '%{http_code}' \
      --max-time 10 \
      -H "Host: ${HOST_HEADER}" \
      "${INGRESS_URL}${POSITIVE_PATH}"
  )"
fi

[[ "${HTTP_CODE}" == "200" ]] || fail "Ingress retornou HTTP ${HTTP_CODE}; esperado=200 (${POSITIVE_PATH})"
pass "Ingress respondeu HTTP 200 em ${POSITIVE_PATH}"

# ============================================================
# 6) Preparar namespace fora do mesh
# ============================================================
info "6) Preparando namespace fora do mesh para teste negativo"

kubectl create namespace "${VALIDATION_NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl label namespace "${VALIDATION_NAMESPACE}" istio-injection=disabled --overwrite >/dev/null

pass "Namespace ${VALIDATION_NAMESPACE} pronto e sem injeção de sidecar"

# ============================================================
# 7) Subir pod temporário fora do mesh
# ============================================================
info "7) Subindo pod temporário fora do mesh"

kubectl delete pod "${NEGATIVE_POD_NAME}" -n "${VALIDATION_NAMESPACE}" --ignore-not-found >/dev/null 2>&1 || true

kubectl run "${NEGATIVE_POD_NAME}" \
  -n "${VALIDATION_NAMESPACE}" \
  --image="${NEGATIVE_IMAGE}" \
  --restart=Never \
  --command -- sh -c 'sleep 300' >/dev/null

kubectl wait --for=condition=Ready pod/"${NEGATIVE_POD_NAME}" -n "${VALIDATION_NAMESPACE}" --timeout=120s >/dev/null

NEGATIVE_CONTAINERS="$(kubectl get pod "${NEGATIVE_POD_NAME}" -n "${VALIDATION_NAMESPACE}" -o jsonpath='{range .spec.initContainers[*]}{.name}{"\n"}{end}{range .spec.containers[*]}{.name}{"\n"}{end}')"
printf '%s\n' "${NEGATIVE_CONTAINERS}" | grep -q '^istio-proxy$' && fail "O pod de teste negativo recebeu sidecar; ele precisa ficar fora do mesh"

pass "Pod de teste negativo está realmente fora do mesh"

# ============================================================
# 8) Teste negativo: acesso direto ao Service deve falhar
# ============================================================
info "8) Teste negativo: acesso direto ao Service deve falhar fora do mesh"

NEGATIVE_OUTPUT="$(
  kubectl exec -n "${VALIDATION_NAMESPACE}" "${NEGATIVE_POD_NAME}" -- sh -c "
    code=\$(curl -sS -o /tmp/body -w '%{http_code}' --max-time 5 '${DIRECT_URL}' 2>/tmp/err)
    rc=\$?
    echo CURL_RC=\$rc
    echo HTTP_CODE=\${code:-000}
    echo STDERR_BEGIN
    cat /tmp/err
    echo STDERR_END
  " 2>&1 || true
)"

NEGATIVE_RC="$(printf '%s\n' "${NEGATIVE_OUTPUT}" | awk -F= '/^CURL_RC=/{print $2}')"
NEGATIVE_HTTP_CODE="$(printf '%s\n' "${NEGATIVE_OUTPUT}" | awk -F= '/^HTTP_CODE=/{print $2}')"

if [[ "${NEGATIVE_RC:-1}" != "0" ]]; then
  pass "Acesso direto ao Service falhou fora do mesh, como esperado (curl rc=${NEGATIVE_RC})"
elif [[ "${NEGATIVE_HTTP_CODE:-000}" != "200" ]]; then
  pass "Acesso direto ao Service não retornou 200 fora do mesh (HTTP ${NEGATIVE_HTTP_CODE}), como esperado"
else
  printf '%s\n' "${NEGATIVE_OUTPUT}"
  fail "O acesso direto fora do mesh retornou 200; isso foge do comportamento esperado com STRICT + mTLS"
fi

# ============================================================
# Resumo final
# ============================================================
info "Validação final"
pass "Mesh da postgres-api validado com sucesso"

echo
echo "Resumo:"
echo "- aplicação disponível"
echo "- sidecar injetado"
echo "- Gateway/VirtualService/DestinationRule/AuthorizationPolicy presentes"
echo "- DestinationRule com ISTIO_MUTUAL"
echo "- PeerAuthentication STRICT presente"
echo "- acesso via ingress OK (${POSITIVE_PATH})"
echo "- acesso direto fora do mesh bloqueado"
