#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${BANKING_NAMESPACE:-banking}"
TIMEOUT="${BANKING_ROLLOUT_TIMEOUT:-300s}"
WORKLOADS=(account-service transaction-service acquirer-service banking-web store-service)

pass() { printf '[PASS] %s\n' "$*"; }
fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || fail "dependência ausente: $1"; }

need kubectl
need jq
kubectl get namespace "$NAMESPACE" >/dev/null 2>&1 || fail "namespace $NAMESPACE não encontrado"

for workload in "${WORKLOADS[@]}"; do
  kubectl -n "$NAMESPACE" rollout status "deployment/$workload" --timeout="$TIMEOUT" >/dev/null
  deployment="$(kubectl -n "$NAMESPACE" get deployment "$workload" -o json)"
  desired="$(jq -r '.spec.replicas // 0' <<<"$deployment")"
  ready="$(jq -r '.status.readyReplicas // 0' <<<"$deployment")"
  ((desired >= 2)) || fail "$workload possui somente $desired réplica(s) desejada(s)"
  ((ready >= 2)) || fail "$workload possui somente $ready réplica(s) pronta(s)"
  jq -e '.spec.template.spec.topologySpreadConstraints[]? | select(.topologyKey == "kubernetes.io/hostname")' \
    >/dev/null <<<"$deployment" || fail "$workload não possui topology spread por hostname"
  jq -e --arg workload "$workload" \
    '.spec.template.spec.containers[] | select(.name == $workload) | .readinessProbe' \
    >/dev/null <<<"$deployment" || fail "$workload não possui Readiness Probe"
  kubectl -n "$NAMESPACE" get poddisruptionbudget "$workload" >/dev/null 2>&1 \
    || fail "$workload não possui PodDisruptionBudget"
  pass "$workload: réplicas=$ready/$desired, topology spread, Readiness Probe e PDB"
done

for workload in account-service transaction-service; do
  min_replicas="$(kubectl -n "$NAMESPACE" get hpa "$workload" -o jsonpath='{.spec.minReplicas}')"
  if [[ ! "$min_replicas" =~ ^[0-9]+$ ]] || ((min_replicas < 2)); then
    fail "HPA/$workload possui minReplicas=${min_replicas:-ausente}"
  fi
  pass "HPA/$workload mantém ao menos duas réplicas"
done

transaction="$(kubectl -n "$NAMESPACE" get deployment transaction-service -o json)"
jq -e '
  .spec.template.spec.containers[]
  | select(.name == "dotnet-monitor") as $monitor
  | (($monitor.args // []) | index("http://127.0.0.1:52323") != null)
    and ($monitor.startupProbe == null)
    and ($monitor.readinessProbe == null)
    and ($monitor.livenessProbe == null)
' >/dev/null <<<"$transaction" \
  || fail "dotnet-monitor deve permanecer em loopback e fora dos probes de rede do kubelet"

transaction_service="$(kubectl -n "$NAMESPACE" get service transaction-service -o json)"
jq -e '
  [.spec.ports[]
    | select(
        .name == "diagnostics"
        or .port == 52323
        or .targetPort == "diagnostics"
        or .targetPort == 52323
      )]
  | length == 0
' >/dev/null <<<"$transaction_service" \
  || fail "Service/transaction-service expõe indevidamente o endpoint do dotnet-monitor"
pass "dotnet-monitor permanece restrito ao pod e não bloqueia a prontidão da aplicação"

policy="$(kubectl get clusterpolicy allow-approved-registries -o json)"
jq -e '[.spec.rules[].validate.foreach[]?.anyPattern[]?.image] | index("registry.istio.io/*") != null' \
  >/dev/null <<<"$policy" || fail "registry.istio.io não está na policy de registries aprovados"
pass "registry.istio.io está explicitamente aprovado"

unhealthy="$(kubectl -n "$NAMESPACE" get pods -o json | jq '[.items[] | select(.status.phase != "Running") | .metadata.name] | length')"
((unhealthy == 0)) || fail "$unhealthy Pod(s) fora de Running"
pass "todos os Pods do namespace estão em Running"

echo "Operational readiness da Banking validada sem expor Secrets ou payloads transacionais."
