#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-apps}"
INGRESS_URL="${INGRESS_URL:-https://192.168.109.151:31882}"
HOST_HEADER="${HOST_HEADER:-nginx.lab.local}"
PROMETHEUS_NS="${PROMETHEUS_NS:-monitoring}"
PROMETHEUS_POD="${PROMETHEUS_POD:-prometheus-kube-prometheus-stack-prometheus-0}"

prometheus_query() {
  local query="$1" encoded
  encoded="$(printf '%s' "${query}" | jq -sRr @uri)"
  kubectl -n "${PROMETHEUS_NS}" exec "${PROMETHEUS_POD}" -- wget -qO- "http://localhost:9090/api/v1/query?query=${encoded}"
}

kubectl -n tracing rollout status deployment/tempo --timeout=180s
kubectl -n tracing rollout status deployment/otel-collector --timeout=180s

INGRESS_AUTHORITY="${INGRESS_URL#https://}"
INGRESS_IP="${INGRESS_AUTHORITY%%:*}"
INGRESS_PORT="${INGRESS_AUTHORITY##*:}"
for _ in $(seq 1 10); do
  status="$(curl -ksS -o /dev/null -w '%{http_code}' --resolve "${HOST_HEADER}:${INGRESS_PORT}:${INGRESS_IP}" "https://${HOST_HEADER}:${INGRESS_PORT}/postgres-api/users")"
  [[ "${status}" == "200" ]] || { echo "GET /users retornou HTTP ${status}" >&2; exit 1; }
done

trace_id=""
for attempt in $(seq 1 9); do
  search="$(kubectl -n tracing run "tempo-search-${attempt}" --image=curlimages/curl:8.10.1 --restart=Never --rm -i --quiet --command -- curl -fsS 'http://tempo:3200/api/search?q=%7Bname%3D%22postgresql.users.select%22%7D')"
  trace_id="$(grep -oE '[0-9a-f]{32}' <<<"${search}" | head -n 1 || true)"
  [[ -n "${trace_id}" ]] && break
  sleep 10
done

[[ -n "${trace_id}" ]] || { echo "Nenhum trace da postgres-api encontrado no Tempo" >&2; exit 1; }
trace="$(kubectl -n tracing run tempo-trace --image=curlimages/curl:8.10.1 --restart=Never --rm -i --quiet --command -- curl -fsS "http://tempo:3200/api/traces/${trace_id}")"
grep -q 'postgres-api' <<<"${trace}"
grep -q 'postgresql.users.select' <<<"${trace}"
if grep -q 'STATUS_CODE_ERROR' <<<"${trace}"; then
  echo "Trace PostgreSQL contém STATUS_CODE_ERROR" >&2
  exit 1
fi
echo "Trace HTTP -> postgres-api -> PostgreSQL validado: ${trace_id}"

calls=""
for attempt in $(seq 1 9); do
  calls="$(prometheus_query 'sum(traces_spanmetrics_calls_total{service="postgres-api",span_kind="SPAN_KIND_SERVER"})' |
    jq -r '.data.result[0].value[1] // empty')"
  [[ -n "${calls}" && "${calls}" != "0" ]] && break
  sleep 10
done
[[ -n "${calls}" && "${calls}" != "0" ]] || {
  echo "Tempo não gerou traces_spanmetrics_calls_total para postgres-api" >&2
  exit 1
}

latency_series="$(prometheus_query 'count(traces_spanmetrics_latency_bucket{service="postgres-api",span_kind="SPAN_KIND_SERVER"})' |
  jq -r '.data.result[0].value[1] // "0"')"
[[ "${latency_series}" != "0" ]] || {
  echo "Tempo não gerou histograma de latência para postgres-api" >&2
  exit 1
}

probe_traces="$(kubectl -n tracing run tempo-probe-search --image=curlimages/curl:8.10.1 --restart=Never --rm -i --quiet --command -- curl -fsS 'http://tempo:3200/api/search?q=%7B%20name%20%3D%20%22postgres-api.http%22%20%26%26%20%28span.url.path%20%3D%20%22%2Fhealth%22%20%7C%7C%20span.url.path%20%3D%20%22%2Fhealthz%22%20%7C%7C%20span.url.path%20%3D%20%22%2Freadyz%22%29%20%7D' |
  grep -oE '[0-9a-f]{32}' | head -n 1 || true)"
[[ -z "${probe_traces}" ]] || {
  echo "Probe indevidamente armazenada no Tempo: ${probe_traces}" >&2
  exit 1
}

echo "Métricas RED validadas: calls=${calls}, latency_series=${latency_series}; probes ausentes."
