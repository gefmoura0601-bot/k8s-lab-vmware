#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-apps}"
INGRESS_URL="${INGRESS_URL:-https://192.168.109.151:31882}"
HOST_HEADER="${HOST_HEADER:-nginx.lab.local}"

kubectl -n tracing rollout status deployment/tempo --timeout=180s
kubectl -n tracing rollout status deployment/otel-collector --timeout=180s

INGRESS_AUTHORITY="${INGRESS_URL#https://}"
INGRESS_IP="${INGRESS_AUTHORITY%%:*}"
INGRESS_PORT="${INGRESS_AUTHORITY##*:}"
for _ in $(seq 1 10); do
  status="$(curl -ksS -o /dev/null -w '%{http_code}' --resolve "${HOST_HEADER}:${INGRESS_PORT}:${INGRESS_IP}" "https://${HOST_HEADER}:${INGRESS_PORT}/postgres-api/users")"
  [[ "${status}" == "200" ]] || { echo "GET /users retornou HTTP ${status}" >&2; exit 1; }
done

sleep 10
search="$(kubectl -n tracing run tempo-search --image=curlimages/curl:8.10.1 --restart=Never --rm -i --quiet --command -- curl -fsS 'http://tempo:3200/api/search?q=%7Bname%3D%22postgresql.users.select%22%7D')"
trace_id="$(grep -oE '[0-9a-f]{32}' <<<"${search}" | head -n 1)"

[[ -n "${trace_id}" ]] || { echo "Nenhum trace da postgres-api encontrado no Tempo" >&2; exit 1; }
trace="$(kubectl -n tracing run tempo-trace --image=curlimages/curl:8.10.1 --restart=Never --rm -i --quiet --command -- curl -fsS "http://tempo:3200/api/traces/${trace_id}")"
grep -q 'postgres-api' <<<"${trace}"
grep -q 'postgresql.users.select' <<<"${trace}"
if grep -q 'STATUS_CODE_ERROR' <<<"${trace}"; then
  echo "Trace PostgreSQL contém STATUS_CODE_ERROR" >&2
  exit 1
fi
echo "Trace HTTP -> postgres-api -> PostgreSQL validado: ${trace_id}"
