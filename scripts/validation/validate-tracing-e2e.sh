#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-apps}"
SERVICE="${SERVICE:-postgres-api}"

kubectl -n tracing rollout status deployment/tempo --timeout=180s
kubectl -n tracing rollout status deployment/otel-collector --timeout=180s

for _ in $(seq 1 10); do
  kubectl -n "${NAMESPACE}" run trace-client --image=curlimages/curl:8.10.1 --restart=Never --rm -i --quiet \
    --command -- curl -fsS "http://${SERVICE}/users" >/dev/null
done

sleep 10
search="$(kubectl -n tracing run tempo-query --image=curlimages/curl:8.10.1 --restart=Never --rm -i --quiet --command -- curl -fsS 'http://tempo:3200/api/search?tags=service.name%3Dpostgres-api&limit=5')"
trace_id="$(grep -oE '[0-9a-f]{32}' <<<"${search}" | head -n 1)"

[[ -n "${trace_id}" ]] || { echo "Nenhum trace da postgres-api encontrado no Tempo" >&2; exit 1; }
trace="$(kubectl -n tracing run tempo-query --image=curlimages/curl:8.10.1 --restart=Never --rm -i --quiet --command -- curl -fsS "http://tempo:3200/api/traces/${trace_id}")"
grep -q 'postgres-api' <<<"${trace}"
grep -q 'postgresql.users.select' <<<"${trace}"
echo "Trace HTTP -> postgres-api -> PostgreSQL validado: ${trace_id}"
