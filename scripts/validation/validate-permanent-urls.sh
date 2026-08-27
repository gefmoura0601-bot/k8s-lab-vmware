#!/usr/bin/env bash
set -euo pipefail

INGRESS_IP="${INGRESS_IP:-192.168.109.151}"
INGRESS_PORT="${INGRESS_PORT:-31882}"
MAX_TIME="${MAX_TIME:-15}"

command -v curl >/dev/null 2>&1 || { echo 'ERRO: curl não encontrado' >&2; exit 1; }

tmp_file="$(mktemp /tmp/permanent-urls.XXXXXX)"
cleanup() {
  case "${tmp_file}" in
    /tmp/permanent-urls.*) rm -f -- "${tmp_file}" ;;
    *) echo "ERRO: arquivo temporário inesperado: ${tmp_file}" >&2 ;;
  esac
}
trap cleanup EXIT INT TERM

check_url() {
  local name="$1" host="$2" path="$3" expected="$4"
  local code

  code="$(curl -ksS --noproxy '*' --max-time "${MAX_TIME}" \
    --resolve "${host}:${INGRESS_PORT}:${INGRESS_IP}" \
    -o "${tmp_file}" -w '%{http_code}' \
    "https://${host}:${INGRESS_PORT}${path}")"

  if [[ "${code}" != "${expected}" ]]; then
    echo "ERRO: ${name} retornou HTTP ${code}; esperado=${expected}" >&2
    return 1
  fi

  printf '[OK] %-12s https://%s:%s%s (HTTP %s)\n' \
    "${name}" "${host}" "${INGRESS_PORT}" "${path}" "${code}"
}

check_url Grafana grafana.lab.local /api/health 200
check_url Prometheus prometheus.lab.local /api/v1/status/runtimeinfo 200
check_url ArgoCD argocd.lab.local / 200
check_url RabbitMQ rabbitmq.lab.local / 200
check_url Bank-Moura bank-moura.lab.local /banking/ 200

echo 'Todos os endpoints permanentes responderam corretamente.'
