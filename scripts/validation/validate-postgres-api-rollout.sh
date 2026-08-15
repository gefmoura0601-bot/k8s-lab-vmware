#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-apps}"
ROLLOUT="${ROLLOUT:-postgres-api}"
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-240}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
ROLLOUT_MANIFEST="${REPO_ROOT}/kubernetes/apps/postgres-api/rollout.yaml"
ARGOCD_APPLICATION="${ARGOCD_APPLICATION:-postgres-api}"

wait_for_phase() {
  local expected="$1" elapsed=0 phase=""
  while (( elapsed < TIMEOUT_SECONDS )); do
    phase="$(kubectl -n "${NAMESPACE}" get rollout "${ROLLOUT}" -o jsonpath='{.status.phase}' 2>/dev/null || true)"
    [[ "${phase}" == "${expected}" ]] && return 0
    sleep 5
    elapsed=$((elapsed + 5))
  done
  kubectl -n "${NAMESPACE}" get rollout "${ROLLOUT}" -o yaml || true
  echo "Timeout aguardando ${expected}; fase atual: ${phase:-desconhecida}" >&2
  return 1
}

restore_rollout() {
  echo "Restaurando a estrategia canary declarada no Git..."
  kubectl -n "${NAMESPACE}" patch rollout "${ROLLOUT}" --type=merge -p '{"spec":{"template":{"metadata":{"annotations":{"lab.k8s.io/rollback-test":null,"lab.k8s.io/guardrail-recovery":null,"lab.openai/rollback-test":null}}}}}' >/dev/null 2>&1 || true
  kubectl apply -f "${ROLLOUT_MANIFEST}" >/dev/null
  kubectl -n "${NAMESPACE}" patch rollout "${ROLLOUT}" --subresource=status --type=merge \
    -p '{"status":{"abort":false}}' >/dev/null 2>&1 || true
  kubectl -n argocd annotate application "${ARGOCD_APPLICATION}" argocd.argoproj.io/skip-reconcile- >/dev/null 2>&1 || true
  kubectl -n argocd annotate application "${ARGOCD_APPLICATION}" argocd.argoproj.io/refresh=hard --overwrite >/dev/null 2>&1 || true
  wait_for_phase Healthy
}
trap restore_rollout EXIT

wait_for_phase Healthy
kubectl -n argocd annotate application "${ARGOCD_APPLICATION}" argocd.argoproj.io/skip-reconcile=true --overwrite >/dev/null
stable_before="$(kubectl -n "${NAMESPACE}" get service postgres-api-stable -o jsonpath='{.spec.selector.rollouts-pod-template-hash}')"
test_id="$(date +%s)"

echo "Iniciando revisao canary controlada com analise propositalmente reprovada..."
patch="$(printf '{"spec":{"strategy":{"canary":{"steps":[{"setWeight":10},{"analysis":{"templates":[{"templateName":"postgres-api-forced-failure"}]}}]}},"template":{"metadata":{"annotations":{"lab.k8s.io/rollback-test":"%s"}}}}}' "${test_id}")"
kubectl -n "${NAMESPACE}" patch rollout "${ROLLOUT}" --type=merge -p "${patch}" >/dev/null
wait_for_phase Degraded

stable_after="$(kubectl -n "${NAMESPACE}" get service postgres-api-stable -o jsonpath='{.spec.selector.rollouts-pod-template-hash}')"
stable_weight="$(kubectl -n "${NAMESPACE}" get virtualservice postgres-api -o jsonpath='{.spec.http[0].route[0].weight}')"
canary_weight="$(kubectl -n "${NAMESPACE}" get virtualservice postgres-api -o jsonpath='{.spec.http[0].route[1].weight}')"
failed_analysis="$(kubectl -n "${NAMESPACE}" get analysisrun --sort-by=.metadata.creationTimestamp -o jsonpath='{range .items[?(@.status.phase=="Failed")]}{.metadata.name}{"\n"}{end}' | tail -n 1)"

[[ -n "${failed_analysis}" ]] || { echo "Nenhuma AnalysisRun reprovada foi encontrada" >&2; exit 1; }
[[ "${stable_before}" == "${stable_after}" ]] || { echo "A revisao estavel mudou durante o rollback" >&2; exit 1; }
[[ "${stable_weight}" == "100" && "${canary_weight}" == "0" ]] || {
  echo "Pesos inesperados: stable=${stable_weight}, canary=${canary_weight}" >&2
  exit 1
}
echo "Rollback validado: AnalysisRun=${failed_analysis}, stable=${stable_weight}%, canary=${canary_weight}%."
