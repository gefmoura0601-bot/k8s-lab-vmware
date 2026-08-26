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
MAX_DURATION_SECONDS="${ASSESSMENT_MAX_DURATION_SECONDS:-1800}"
ACTIVE_PID=""
ACTIVE_COMPONENT=""
COLLECTION_CANCELLED=0
COLLECTION_TIMED_OUT=0
COLLECTION_STARTED_EPOCH=0
WEB_MANAGED_BY_MENU=0

[[ "$MAX_DURATION_SECONDS" =~ ^[0-9]+$ ]] || MAX_DURATION_SECONDS=1800
((MAX_DURATION_SECONDS < 60)) && MAX_DURATION_SECONDS=60
((MAX_DURATION_SECONDS > 7200)) && MAX_DURATION_SECONDS=7200

terminate_active(){
  local attempt
  [[ -n "$ACTIVE_PID" ]] || return 0
  if kill -0 "$ACTIVE_PID" 2>/dev/null; then
    echo "Encerrando $ACTIVE_COMPONENT (PID $ACTIVE_PID)..." >&2
    kill -TERM -- "-$ACTIVE_PID" 2>/dev/null || kill -TERM "$ACTIVE_PID" 2>/dev/null || true
    for attempt in {1..50}; do
      kill -0 "$ACTIVE_PID" 2>/dev/null || break
      sleep 0.1
    done
    if kill -0 "$ACTIVE_PID" 2>/dev/null; then
      kill -KILL -- "-$ACTIVE_PID" 2>/dev/null || kill -KILL "$ACTIVE_PID" 2>/dev/null || true
    fi
    wait "$ACTIVE_PID" 2>/dev/null || true
  fi
  ACTIVE_PID=""; ACTIVE_COMPONENT=""
}

cancel_on_signal(){
  COLLECTION_CANCELLED=1
  echo >&2
  echo "Cancelamento solicitado; preservando a coleta parcial como CANCELLED." >&2
  terminate_active
}

cleanup_menu(){
  terminate_active
  if ((WEB_MANAGED_BY_MENU == 1)) && command -v curl >/dev/null 2>&1; then
    curl -fsS -X POST "http://127.0.0.1:$PORT/cancel" >/dev/null 2>&1 || true
  fi
}

remaining_seconds(){
  local elapsed
  elapsed=$(( $(date +%s) - COLLECTION_STARTED_EPOCH ))
  printf '%s\n' $((MAX_DURATION_SECONDS - elapsed))
}

run_bounded(){
  local component="$1" component_timeout="$2" logfile="$3" remaining effective rc
  shift 3
  remaining="$(remaining_seconds)"
  if ((remaining <= 0)); then
    COLLECTION_TIMED_OUT=1
    printf 'Tempo total de %ss esgotado antes de %s.\n' "$MAX_DURATION_SECONDS" "$component" | tee -a "$logfile"
    return 124
  fi
  effective="$component_timeout"; ((effective > remaining)) && effective="$remaining"
  ACTIVE_COMPONENT="$component"
  printf '[%s] %s (limite %ss; restante total %ss)\n' "$(date -u +%FT%TZ)" "$component" "$effective" "$remaining" | tee -a "$logfile"
  setsid timeout --signal=TERM --kill-after=10s "${effective}s" "$@" > >(tee -a "$logfile") 2>&1 &
  ACTIVE_PID=$!
  if wait "$ACTIVE_PID"; then rc=0; else rc=$?; fi
  ACTIVE_PID=""; ACTIVE_COMPONENT=""
  if ((COLLECTION_CANCELLED == 1)); then return 130; fi
  if ((rc == 124 || rc == 137)); then COLLECTION_TIMED_OUT=1; return 124; fi
  return "$rc"
}

run_bounded_capture(){
  local component="$1" component_timeout="$2" stdout_file="$3" stderr_file="$4" remaining effective rc
  shift 4
  remaining="$(remaining_seconds)"
  if ((remaining <= 0)); then COLLECTION_TIMED_OUT=1; return 124; fi
  effective="$component_timeout"; ((effective > remaining)) && effective="$remaining"
  ACTIVE_COMPONENT="$component"
  printf '[%s] %s (limite %ss; restante total %ss)\n' "$(date -u +%FT%TZ)" "$component" "$effective" "$remaining" | tee -a "$stderr_file"
  setsid timeout --signal=TERM --kill-after=10s "${effective}s" "$@" > "$stdout_file" 2> >(tee -a "$stderr_file" >&2) &
  ACTIVE_PID=$!
  if wait "$ACTIVE_PID"; then rc=0; else rc=$?; fi
  ACTIVE_PID=""; ACTIVE_COMPONENT=""
  if ((COLLECTION_CANCELLED == 1)); then return 130; fi
  if ((rc == 124 || rc == 137)); then COLLECTION_TIMED_OUT=1; return 124; fi
  return "$rc"
}

trap cancel_on_signal INT TERM
trap cleanup_menu EXIT

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
  local status="${9:-$([[ "$completed" == true ]] && echo COMPLETED || echo FAILED)}" reason="${10:-}" max_duration="${11:-$MAX_DURATION_SECONDS}" created
  created="$(jq -r '.createdAt // empty' "$out/metadata.json" 2>/dev/null || true)"; created="${created:-$(date -u +%FT%TZ)}"
  jq -n --arg id "$id" --arg phase "$phase" --arg created "$created" --arg finished "$(date -u +%FT%TZ)" \
    --arg cluster "$cluster_name" --arg context "$cluster_context" --arg status "$status" --arg reason "$reason" \
    --argjson baseline "$baseline" --argjson completed "$completed" --argjson codes "$codes" --argjson maxDuration "$max_duration" \
    '{id:$id,phase:$phase,createdAt:$created,finishedAt:(if $status=="RUNNING" then null else $finished end),clusterName:$cluster,context:$context,baseline:$baseline,status:$status,completed:$completed,cancelled:($status=="CANCELLED"),cancelReason:(if $reason=="" then null else $reason end),maxDurationSeconds:$maxDuration,readOnly:true,collectorExitCodes:$codes}' > "$out/metadata.json"
  cp "$out/metadata.json" "$out/menu-metadata.json"
}

collect(){
  local phase="$1" label id out prom_url prom_window answer cluster_context cluster_name eks_name status reason
  local assess_rc=125 discovery_rc=125 telemetry_rc=125 scanner_rc=125 validator_rc=125 completed=false baseline=false codes code
  COLLECTION_CANCELLED=0; COLLECTION_TIMED_OUT=0; COLLECTION_STARTED_EPOCH="$(date +%s)"
  read -r -p "Identificador da mudança ($phase): " label
  label="${label:-manual}"; label="${label//[^a-zA-Z0-9._-]/-}"
  prom_url="${PROMETHEUS_URL:-}"; prom_window="${PROMETHEUS_WINDOW:-7d}"
  read -r -p "URL explícita do Prometheus (Enter = ${prom_url:-DISABLED}): " answer
  prom_url="${answer:-$prom_url}"
  read -r -p "Janela Prometheus 1d/3d/7d/14d/30d [${prom_window}]: " answer
  prom_window="${answer:-$prom_window}"; [[ "$prom_window" =~ ^(1d|3d|7d|14d|30d)$ ]] || prom_window=7d

  id="eks-$(date -u +%Y%m%dT%H%M%SZ)-${phase}-${label}"; out="$OUTROOT/$id"; mkdir -p "$out"
  IFS=$'\t' read -r cluster_context cluster_name eks_name < <(cluster_identity)
  [[ "$phase" == before ]] && baseline=true
  write_metadata "$out" "$id" "$phase" "$cluster_name" "$cluster_context" "$baseline" false '[]' RUNNING '' "$MAX_DURATION_SECONDS"
  echo "== Coleta $phase: $id | cluster: $cluster_name | limite total: ${MAX_DURATION_SECONDS}s =="
  echo "Ctrl+C cancela toda a árvore; dados parciais serão preservados."

  if run_bounded assessment 600 "$out/assessment.log" env OUTPUT_DIR="$out" EKS_CLUSTER_NAME="$eks_name" ASSESSMENT_MAX_DURATION_SECONDS="$MAX_DURATION_SECONDS" bash "$ASSESS"; then assess_rc=0; else assess_rc=$?; fi
  if ((COLLECTION_CANCELLED == 0 && COLLECTION_TIMED_OUT == 0)); then
    if run_bounded discovery 900 "$out/discovery.log" bash "$DISCOVERY" --output-dir "$out/discovery" --combined-report; then discovery_rc=0; else discovery_rc=$?; fi
  fi
  if ((COLLECTION_CANCELLED == 0 && COLLECTION_TIMED_OUT == 0)); then
    if [[ -n "$prom_url" ]]; then
      if run_bounded_capture prometheus 1200 "$out/prometheus-telemetry.json" "$out/prometheus-telemetry.log" python3.11 "$TELEMETRY" --url "$prom_url" --window "$prom_window" --workloads-file "$out/workloads.json"; then telemetry_rc=0; else telemetry_rc=$?; fi
    else
      printf '%s\n' '{"state":"DISABLED","reason":"PROMETHEUS_URL not explicitly configured","workloads":[]}' > "$out/prometheus-telemetry.json"
      telemetry_rc=0
    fi
  fi
  if ((COLLECTION_CANCELLED == 0 && COLLECTION_TIMED_OUT == 0)); then
    if run_bounded comprehensive "$MAX_DURATION_SECONDS" "$out/comprehensive-assessment.log" python3.11 "$SCANNER" --snapshot-dir "$out" --collect-live --timeout 30 --chunk-size 200 --inventory-workers "${ASSESSMENT_WORKERS:-4}" --api-delay-ms "${ASSESSMENT_API_DELAY_MS:-100}" --max-requests "${ASSESSMENT_MAX_REQUESTS:-1500}" --max-duration "$MAX_DURATION_SECONDS" --max-response-mb "${ASSESSMENT_MAX_RESPONSE_MB:-512}" --resume; then scanner_rc=0; else scanner_rc=$?; fi
  fi
  if ((COLLECTION_CANCELLED == 0 && COLLECTION_TIMED_OUT == 0)); then
    if run_bounded artifact-validation 300 "$out/artifact-smoke.log" python3.11 "$VALIDATOR" "$out"; then validator_rc=0; else validator_rc=$?; fi
  fi

  codes="$(jq -nc --argjson a "$assess_rc" --argjson d "$discovery_rc" --argjson t "$telemetry_rc" --argjson s "$scanner_rc" --argjson v "$validator_rc" '[$a,$d,$t,$s,$v]')"
  status=COMPLETED; reason=''; completed=true
  if ((COLLECTION_CANCELLED == 1)); then status=CANCELLED; reason='operator requested cancellation'; completed=false
  elif ((COLLECTION_TIMED_OUT == 1)); then status=TIMED_OUT; reason="collection exceeded ${MAX_DURATION_SECONDS}s"; completed=false
  else
    for code in "$assess_rc" "$discovery_rc" "$telemetry_rc" "$scanner_rc" "$validator_rc"; do ((code == 0)) || { status=FAILED; completed=false; }; done
  fi
  write_metadata "$out" "$id" "$phase" "$cluster_name" "$cluster_context" "$baseline" "$completed" "$codes" "$status" "$reason" "$MAX_DURATION_SECONDS"
  COLLECTION_STARTED_EPOCH=0
  echo "Salvo em $out | status: $status"
  echo "Contexto: $cluster_context | códigos [assessment, discovery, Prometheus, scanner, smoke]: $codes"
  return 0
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
  WEB_MANAGED_BY_MENU=1
  host="$(hostname -I | awk '{print $1}')"
  echo "Abra: http://$host:$PORT"
  echo "Log: $log"
}

need kubectl; need jq; need timeout; need setsid; mkdir -p "$OUTROOT"
while :; do
  cat <<EOF

=== EKS ASSESSMENT MENU (READ-ONLY | LIMITE ${MAX_DURATION_SECONDS}s) ===
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
