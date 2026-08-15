#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-monitoring}"
RULE="${RULE:-tempo-derived-metrics-controlled-test}"
ALERT="TempoDerivedMetricsControlledTest"
PROMETHEUS_POD="${PROMETHEUS_POD:-prometheus-kube-prometheus-stack-prometheus-0}"

# shellcheck disable=SC2317
cleanup() {
  kubectl -n "${NAMESPACE}" delete prometheusrule "${RULE}" --ignore-not-found >/dev/null
}
trap cleanup EXIT

kubectl apply -f - <<EOF
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: ${RULE}
  namespace: ${NAMESPACE}
  labels:
    release: kube-prometheus-stack
spec:
  groups:
    - name: tempo-derived-metrics-controlled-test
      rules:
        - alert: ${ALERT}
          expr: vector(1)
          labels:
            severity: info
            team: platform
          annotations:
            summary: Teste controlado do pipeline de alertas
EOF

query='ALERTS{alertname="'"${ALERT}"'",alertstate="firing"}'
encoded="$(printf '%s' "${query}" | jq -sRr @uri)"
for _ in $(seq 1 12); do
  firing="$(kubectl -n "${NAMESPACE}" exec "${PROMETHEUS_POD}" -- wget -qO- "http://localhost:9090/api/v1/query?query=${encoded}" |
    jq -r '.data.result | length')"
  if [[ "${firing}" -gt 0 ]]; then
    echo "Alerta controlado ${ALERT} chegou ao estado firing."
    exit 0
  fi
  sleep 10
done

echo "Alerta controlado ${ALERT} não chegou ao estado firing" >&2
exit 1
