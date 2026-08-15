#!/usr/bin/env bash
set -euo pipefail

INGRESS_URL="${INGRESS_URL:-https://192.168.109.151:31882}"
HOST_HEADER="${HOST_HEADER:-nginx.lab.local}"
authority="${INGRESS_URL#https://}"
ip="${authority%%:*}"
port="${authority##*:}"
resolve=(--resolve "${HOST_HEADER}:${port}:${ip}")

kubectl -n banking rollout status deployment/banking-web --timeout=180s
html="$(curl -ksS "${resolve[@]}" "https://${HOST_HEADER}:${port}/banking/")"
grep -q '<title>Atlas Banking</title>' <<<"${html}"
accounts_status="$(curl -ksS -o /tmp/banking-accounts.json -w '%{http_code}' "${resolve[@]}" "https://${HOST_HEADER}:${port}/bank/accounts")"
[[ "${accounts_status}" == "200" ]] || { echo "Contas retornaram HTTP ${accounts_status}" >&2; exit 1; }
jq -e 'type == "array"' /tmp/banking-accounts.json >/dev/null
echo "Banking Web e integração com account-service validados."
