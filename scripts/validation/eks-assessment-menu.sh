#!/usr/bin/env bash
# Interactive read-only EKS/Kubernetes assessment operator menu.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUTROOT="${ASSESSMENT_ROOT:-$ROOT/assessment}"
ASSESS="$ROOT/scripts/validation/assess-eks.sh"
DISCOVERY="$ROOT/scripts/validation/eks-cluster-discovery.sh"
TELEMETRY="$ROOT/scripts/validation/prometheus_telemetry.py"
SCANNER="$ROOT/scripts/validation/eks_comprehensive_assessment.py"
VALIDATOR="$ROOT/scripts/validation/validate_assessment_artifacts.py"
PORT="${DASHBOARD_PORT:-8765}"

need(){ command -v "$1" >/dev/null || { echo "ERRO: $1 ausente" >&2; exit 1; }; }
collections(){ find "$OUTROOT" -mindepth 1 -maxdepth 1 -type d -name 'eks-*' -printf '%f\n' 2>/dev/null | sort; }

cluster_identity(){
  local context ref name eks_name="${EKS_CLUSTER_NAME:-}"
  context="$(kubectl config current-context 2>/dev/null || true)"
  ref="$(kubectl config view --minify -o jsonpath='{.contexts[0].context.cluster}' 2>/dev/null || true)"
  if [[ -z "$eks_name" && "$ref" =~ arn:aws:eks:[^:]+:[^:]+:cluster/(.+)$ ]]; then eks_name="${BASH_REMATCH[1]}"; fi
  if [[ -z "$eks_name" && "$context" =~ arn:aws:eks:[^:]+:[^:]+:cluster/(.+)$ ]]; then eks_name="${BASH_REMATCH[1]}"; fi
  name="${eks_name:-${ref:-${context##*@}}}"
  printf '%s\t%s\t%s\n' "${context:-cluster}" "${name:-cluster}" "$eks_name"
}

write_metadata(){
  local out="$1" id="$2" phase="$3" cluster_name="$4" cluster_context="$5" baseline="$6" completed="$7" codes="$8"
  jq -n --arg id "$id" --arg phase "$phase" --arg created "$(date -u +%FT%TZ)" \
    --arg cluster "$cluster_name" --arg context "$cluster_context" \
    --argjson baseline "$baseline" --argjson completed "$completed" --argjson codes "$codes" \
    '{id:$id,phase:$phase,createdAt:$created,clusterName:$cluster,context:$context,baseline:$baseline,completed:$completed,readOnly:true,collectorExitCodes:$codes}' > "$out/metadata.json"
  cp "$out/metadata.json" "$out/menu-metadata.json"
}

collect(){
  local phase="$1" label id out prom_url prom_window answer cluster_context cluster_name eks_name
  local assess_rc discovery_rc telemetry_rc scanner_rc validator_rc completed baseline codes code
  read -r -p "Identificador da mudança ($phase): " label
  label="${label:-manual}"; label="${label//[^a-zA-Z0-9._-]/-}"
  id="eks-$(date -u +%Y%m%dT%H%M%SZ)-${phase}-${label}"; out="$OUTROOT/$id"; mkdir -p "$out"
  IFS=$'\t' read -r cluster_context cluster_name eks_name < <(cluster_identity)
  echo "== Coleta $phase: $id | cluster: $cluster_name =="

  set +e
  OUTPUT_DIR="$out" EKS_CLUSTER_NAME="$eks_name" bash "$ASSESS" | tee "$out/assessment.log"
  assess_rc="${PIPESTATUS[0]}"
  bash "$DISCOVERY" --output-dir "$out/discovery" --combined-report | tee "$out/discovery.log"
  discovery_rc="${PIPESTATUS[0]}"
  set -e

  prom_url="${PROMETHEUS_URL:-}"; prom_window="${PROMETHEUS_WINDOW:-7d}"
  read -r -p "URL explícita do Prometheus (Enter = ${prom_url:-DISABLED}): " answer
  prom_url="${answer:-$prom_url}"
  read -r -p "Janela Prometheus 1d/3d/7d/14d/30d [${prom_window}]: " answer
  prom_window="${answer:-$prom_window}"
  [[ "$prom_window" =~ ^(1d|3d|7d|14d|30d)$ ]] || prom_window=7d
  if [[ -n "$prom_url" ]]; then
    set +e
    python3.11 "$TELEMETRY" --url "$prom_url" --window "$prom_window" --workloads-file "$out/workloads.json" > "$out/prometheus-telemetry.json" 2> "$out/prometheus-telemetry.log"
    telemetry_rc=$?
    set -e
  else
    printf '%s\n' '{"state":"DISABLED","reason":"PROMETHEUS_URL not explicitly configured","workloads":[]}' > "$out/prometheus-telemetry.json"
    telemetry_rc=0
  fi

  set +e
  python3.11 "$SCANNER" --snapshot-dir "$out" --collect-live --timeout 30 --chunk-size 200 | tee "$out/comprehensive-assessment.log"
  scanner_rc="${PIPESTATUS[0]}"
  set -e

  completed=true
  for code in "$assess_rc" "$discovery_rc" "$telemetry_rc" "$scanner_rc"; do ((code == 0)) || completed=false; done
  baseline=false; [[ "$phase" == before ]] && baseline=true
  codes="$(jq -nc --argjson a "$assess_rc" --argjson d "$discovery_rc" --argjson t "$telemetry_rc" --argjson s "$scanner_rc" '[$a,$d,$t,$s]')"
  write_metadata "$out" "$id" "$phase" "$cluster_name" "$cluster_context" "$baseline" "$completed" "$codes"

  set +e
  python3.11 "$VALIDATOR" "$out" | tee "$out/artifact-smoke.log"
  validator_rc="${PIPESTATUS[0]}"
  set -e
  codes="$(jq -nc --argjson current "$codes" --argjson validator "$validator_rc" '$current + [$validator]')"
  ((validator_rc == 0)) || completed=false
  write_metadata "$out" "$id" "$phase" "$cluster_name" "$cluster_context" "$baseline" "$completed" "$codes"
  echo "Salvo em $out"
  echo "Contexto: $cluster_context | códigos [assessment, discovery, Prometheus, scanner, smoke]: $codes"
}

compare(){
  local before after
  collections | nl -ba
  read -r -p 'ID ANTES: ' before; read -r -p 'ID DEPOIS: ' after
  [[ "$before" =~ ^[A-Za-z0-9._-]+$ && "$after" =~ ^[A-Za-z0-9._-]+$ ]] || { echo 'IDs inválidos.'; return; }
  before="$OUTROOT/$before"; after="$OUTROOT/$after"
  [[ -r "$before/comprehensive-assessment.json" && -r "$after/comprehensive-assessment.json" ]] || { echo 'Coletas abrangentes inválidas.'; return; }
  jq -n --slurpfile before "$before/comprehensive-assessment.json" --slurpfile after "$after/comprehensive-assessment.json" '
    ($before[0].findings | map(select(.severity=="CRIT" or .severity=="WARN") | .id)) as $old |
    ($after[0].findings | map(select(.severity=="CRIT" or .severity=="WARN") | .id)) as $new |
    {before:$before[0].summary,after:$after[0].summary,
     delta:{critical:($after[0].summary.critical-$before[0].summary.critical),warnings:($after[0].summary.warnings-$before[0].summary.warnings),passed:($after[0].summary.passed-$before[0].summary.passed)},
     newRisks:[$after[0].findings[] | select((.severity=="CRIT" or .severity=="WARN") and (.id as $id | ($old | index($id) | not)))],
     resolvedRisks:[$before[0].findings[] | select((.severity=="CRIT" or .severity=="WARN") and (.id as $id | ($new | index($id) | not)))]}'
}

terminal(){
  local id dir
  collections | nl -ba; read -r -p 'ID da coleta: ' id
  [[ "$id" =~ ^[A-Za-z0-9._-]+$ ]] || { echo 'ID inválido.'; return; }
  dir="$OUTROOT/$id"; [[ -d "$dir" ]] || { echo 'Coleta não encontrada.'; return; }
  clear; echo 'EKS ENVIRONMENT - DASHBOARD TERMINAL'
  jq -r '"Coleta: \(.id) | cluster: \(.clusterName) | \(.phase) | \(.createdAt)"' "$dir/metadata.json" 2>/dev/null || true
  jq -r '"Discovery: \(.succeeded)/\(.sections) | N/A: \(.not_applicable) | indisponíveis: \(.unavailable)"' "$dir/discovery/summary.json" 2>/dev/null || true
  echo; column -t -s $'\t' "$dir/findings.tsv" 2>/dev/null || cat "$dir/findings.tsv"
  echo; column -t -s $'\t' "$dir/prometheus-baseline.tsv" 2>/dev/null || true
  if [[ -r "$dir/comprehensive-assessment.json" ]]; then
    echo; echo '== ASSESSMENT ABRANGENTE =='
    jq '.summary' "$dir/comprehensive-assessment.json"
    jq -r '.findings[] | select(.severity == "CRIT" or .severity == "WARN") | [.severity,.category,.namespace,.workload,.check,.detail] | @tsv' "$dir/comprehensive-assessment.json" | head -50 | column -t -s $'\t'
  fi
}

web(){
  local pid="$OUTROOT/dashboard-$PORT.pid" log="$OUTROOT/dashboard-$PORT.log" host
  if [[ -r "$pid" ]] && kill -0 "$(cat "$pid")" 2>/dev/null; then
    echo "Dashboard ativo (PID $(cat "$pid"))."
  else
    need python3.11
    nohup python3.11 "$ROOT/scripts/validation/assessment_dashboard.py" --root "$OUTROOT" --static "$ROOT/app/eks-assessment-dashboard/public" --host 0.0.0.0 --port "$PORT" > "$log" 2>&1 &
    echo $! > "$pid"; sleep 1
  fi
  host="$(hostname -I | awk '{print $1}')"
  echo "Abra: http://$host:$PORT"
  echo "Log: $log"
}

need kubectl; need jq; mkdir -p "$OUTROOT"
while :; do
  cat <<EOF

=== EKS ASSESSMENT MENU (READ-ONLY) ===
1) Coleta ANTES do deploy
2) Coleta DEPOIS do deploy
3) Comparar coletas
4) Dashboard no terminal
5) Publicar dashboard web (porta $PORT)
0) Sair
EOF
  read -r -p 'Opção: ' op
  case "$op" in
    1) collect before;; 2) collect after;; 3) compare;; 4) terminal;;
    5) web;; 0) exit 0;; *) echo 'Opção inválida.';;
  esac
  [[ "$op" == 0 ]] || read -r -p 'Enter para continuar…' _
done
