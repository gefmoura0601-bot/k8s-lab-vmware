#!/usr/bin/env bash
set -euo pipefail

# Read-only EKS checklist. It never mutates Kubernetes/AWS resources, gates,
# baselines or snapshots. Optional Prometheus telemetry is stdout-only.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TELEMETRY="${SCRIPT_DIR}/prometheus_telemetry.py"
TOPOLOGY="${SCRIPT_DIR}/checklist-eks-topology.sh"
WINDOW="${PROMETHEUS_WINDOW:-7d}"
WORKLOADS="${PROMETHEUS_WORKLOADS:-}"
PYTHON_BIN="${PYTHON_BIN:-}"
pass=0; warn=0; fail=0; na=0
ok() { printf '[PASS] %s — %s\n' "$1" "$2"; ((pass+=1)); }
warning() { printf '[WARN] %s — %s\n' "$1" "$2"; ((warn+=1)); }
critical() { printf '[FAIL] %s — %s\n' "$1" "$2"; ((fail+=1)); }
not_evaluated() { printf '[N/A ] %s — %s\n' "$1" "$2"; ((na+=1)); }
json_count() { jq -r "$1"; }
require() { command -v "$1" >/dev/null 2>&1 || { critical "Dependência" "$1 não encontrado"; exit 1; }; }
select_python() { local candidate; [[ -n "$PYTHON_BIN" ]] && return; for candidate in python3.12 python3.11 python3.10 python3; do command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null && { PYTHON_BIN="$candidate"; return; }; done; }

check_cluster_health() {
  local nodes pods unready pressure pending failed
  nodes="$(kubectl get nodes -o json)"; pods="$(kubectl get pods -A -o json)"
  unready="$(printf '%s' "$nodes" | json_count '[.items[] | select(any(.status.conditions[]?; .type == "Ready" and .status != "True"))] | length')"
  pressure="$(printf '%s' "$nodes" | json_count '[.items[] | select(any(.status.conditions[]?; (.type == "MemoryPressure" or .type == "DiskPressure" or .type == "PIDPressure") and .status == "True"))] | length')"
  pending="$(printf '%s' "$pods" | json_count '[.items[] | select(.status.phase == "Pending")] | length')"; failed="$(printf '%s' "$pods" | json_count '[.items[] | select(.status.phase == "Failed")] | length')"
  if [[ "$unready" == 0 ]]; then ok "Cluster nodes" "todos Ready"; else critical "Cluster nodes" "$unready NotReady"; fi
  if [[ "$pressure" == 0 ]]; then ok "Node pressure" "sem pressão"; else critical "Node pressure" "$pressure node(s) sob pressão"; fi
  if [[ "$pending" == 0 ]]; then ok "Pods Pending" "nenhum"; else warning "Pods Pending" "$pending pod(s)"; fi
  if [[ "$failed" == 0 ]]; then ok "Pods Failed" "nenhum"; else warning "Pods Failed" "$failed pod(s)"; fi
}

check_baseline_practices() {
  local pods netpol pdb hpa missing latest privileged pvc
  pods="$(kubectl get pods -A -o json)"; netpol="$(kubectl get netpol -A -o json | json_count '.items | length')"; pdb="$(kubectl get pdb -A -o json | json_count '.items | length')"; hpa="$(kubectl get hpa -A -o json | json_count '.items | length')"
  missing="$(printf '%s' "$pods" | json_count '[.items[] | .spec.containers[]? | select(.resources.requests == null or .resources.limits == null)] | length')"; latest="$(printf '%s' "$pods" | json_count '[.items[] | .spec.containers[]? | select(.image | test("(:latest$|^[^:]+$)"))] | length')"; privileged="$(printf '%s' "$pods" | json_count '[.items[] | .spec.containers[]? | select(.securityContext.privileged == true)] | length')"; pvc="$(kubectl get pvc -A -o json | json_count '[.items[] | select(.status.phase != "Bound")] | length')"
  if ((netpol > 0)); then ok "NetworkPolicy" "$netpol policy object(s)"; else warning "NetworkPolicy" "nenhuma policy"; fi
  if ((pdb > 0)); then ok "PDB" "$pdb PDB(s)"; else warning "PDB" "nenhum PDB"; fi
  if ((hpa > 0)); then ok "HPA" "$hpa HPA(s)"; else warning "HPA" "nenhum HPA"; fi
  if [[ "$missing" == 0 ]]; then ok "Requests/Limits" "todos definidos"; else warning "Requests/Limits" "$missing container(s) sem recursos"; fi
  if [[ "$latest" == 0 ]]; then ok "Image tags" "sem latest/untagged"; else warning "Image tags" "$latest image(ns)"; fi
  if [[ "$privileged" == 0 ]]; then ok "Privileged" "nenhum"; else critical "Privileged" "$privileged container(s)"; fi
  if [[ "$pvc" == 0 ]]; then ok "PVC" "todos Bound"; else warning "PVC" "$pvc PVC(s) não Bound"; fi
}

check_telemetry() {
  [[ -n "${PROMETHEUS_URL:-}" ]] || { not_evaluated "Prometheus" "URL não configurada"; return; }
  select_python || { critical "Prometheus" "Python 3.10+ não encontrado"; return; }
  local args=(--url "$PROMETHEUS_URL" --window "$WINDOW") item; IFS=',' read -r -a items <<<"$WORKLOADS"; for item in "${items[@]}"; do [[ -n "$item" ]] && args+=(--workload "$item"); done
  "$PYTHON_BIN" "$TELEMETRY" "${args[@]}"; warning "Prometheus" "informativo; não altera gates/baselines"
}

require kubectl; require jq; [[ -r "$TOPOLOGY" ]] || { echo "ERRO: módulo de topologia ausente" >&2; exit 1; }
# shellcheck source=tools/eks-assessment/src/checklist-eks-topology.sh
source "$TOPOLOGY"
printf '\n== EKS CHECKLIST (somente leitura) ==\n'; check_cluster_health; check_baseline_practices; run_topology_checks; check_telemetry
printf '\nResumo: PASS=%s WARN=%s FAIL=%s NÃO_AVALIADO=%s\n' "$pass" "$warn" "$fail" "$na"