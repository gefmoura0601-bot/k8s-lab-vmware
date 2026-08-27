#!/usr/bin/env bash
# Interactive read-only EKS/Kubernetes assessment operator menu.
set -euo pipefail

TOOL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPOSITORY_ROOT="$(cd "$TOOL_ROOT/../.." && pwd)"
OUTROOT="${ASSESSMENT_ROOT:-$REPOSITORY_ROOT/assessment}"
ASSESS="$TOOL_ROOT/src/assess-eks.sh"
DISCOVERY="$TOOL_ROOT/src/eks-cluster-discovery.sh"
TELEMETRY="$TOOL_ROOT/src/prometheus_telemetry.py"
SCANNER="$TOOL_ROOT/src/eks_comprehensive_assessment.py"
VALIDATOR="$TOOL_ROOT/src/validate_assessment_artifacts.py"
PREFLIGHT="$TOOL_ROOT/src/assessment-preflight.sh"
PYTHON_BIN="${PYTHON_BIN:-}"
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
    for ((attempt=0; attempt<50; attempt++)); do
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
select_python(){
  local candidate
  if [[ -n "$PYTHON_BIN" ]]; then
    command -v "$PYTHON_BIN" >/dev/null 2>&1 && "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)' >/dev/null 2>&1
    return
  fi
  for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)' >/dev/null 2>&1; then
      PYTHON_BIN="$candidate"; return 0
    fi
  done
  return 1
}
run_preflight(){
  PYTHON_BIN="$PYTHON_BIN" PROMETHEUS_URL="${1:-}" EKS_CLUSTER_NAME="${2:-${EKS_CLUSTER_NAME:-}}" bash "$PREFLIGHT"
}
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
  COLLECTION_CANCELLED=0; COLLECTION_TIMED_OUT=0; COLLECTION_STARTED_EPOCH=0
  read -r -p "Identificador da mudança ($phase): " label
  label="${label:-manual}"; label="${label//[^a-zA-Z0-9._-]/-}"
  prom_url="${PROMETHEUS_URL:-}"; prom_window="${PROMETHEUS_WINDOW:-7d}"
  read -r -p "URL explícita do Prometheus (Enter = ${prom_url:-DISABLED}): " answer
  prom_url="${answer:-$prom_url}"
  read -r -p "Janela Prometheus 1d/3d/7d/14d/30d [${prom_window}]: " answer
  prom_window="${answer:-$prom_window}"; [[ "$prom_window" =~ ^(1d|3d|7d|14d|30d)$ ]] || prom_window=7d

  IFS=$'\t' read -r cluster_context cluster_name eks_name < <(cluster_identity)
  if ! run_preflight "$prom_url" "$eks_name"; then
    echo "Coleta não iniciada: corrija os itens FAIL do preflight." >&2
    return 1
  fi
  COLLECTION_STARTED_EPOCH="$(date +%s)"
  id="eks-$(date -u +%Y%m%dT%H%M%SZ)-${phase}-${label}"; out="$OUTROOT/$id"; mkdir -p "$out"
  [[ "$phase" == before ]] && baseline=true
  write_metadata "$out" "$id" "$phase" "$cluster_name" "$cluster_context" "$baseline" false '[]' RUNNING '' "$MAX_DURATION_SECONDS"
  echo "== Coleta $phase: $id | cluster: $cluster_name | limite total: ${MAX_DURATION_SECONDS}s =="
  echo "Ctrl+C cancela toda a árvore; dados parciais serão preservados."

  if run_bounded assessment 600 "$out/assessment.log" env OUTPUT_DIR="$out" EKS_CLUSTER_NAME="$eks_name" PYTHON_BIN="$PYTHON_BIN" ASSESSMENT_MAX_DURATION_SECONDS="$MAX_DURATION_SECONDS" bash "$ASSESS"; then assess_rc=0; else assess_rc=$?; fi
  if ((COLLECTION_CANCELLED == 0 && COLLECTION_TIMED_OUT == 0)); then
    if run_bounded discovery 900 "$out/discovery.log" bash "$DISCOVERY" --output-dir "$out/discovery" --combined-report; then discovery_rc=0; else discovery_rc=$?; fi
  fi
  if ((COLLECTION_CANCELLED == 0 && COLLECTION_TIMED_OUT == 0)); then
    if [[ -n "$prom_url" ]]; then
      if run_bounded_capture prometheus 1200 "$out/prometheus-telemetry.json" "$out/prometheus-telemetry.log" "$PYTHON_BIN" "$TELEMETRY" --url "$prom_url" --window "$prom_window" --workloads-file "$out/workloads.json"; then telemetry_rc=0; else telemetry_rc=$?; fi
    else
      printf '%s\n' '{"state":"DISABLED","reason":"PROMETHEUS_URL not explicitly configured","workloads":[]}' > "$out/prometheus-telemetry.json"
      telemetry_rc=0
    fi
  fi
  if ((COLLECTION_CANCELLED == 0 && COLLECTION_TIMED_OUT == 0)); then
    if run_bounded comprehensive "$MAX_DURATION_SECONDS" "$out/comprehensive-assessment.log" "$PYTHON_BIN" "$SCANNER" --snapshot-dir "$out" --collect-live --timeout 30 --chunk-size 200 --inventory-workers "${ASSESSMENT_WORKERS:-4}" --api-delay-ms "${ASSESSMENT_API_DELAY_MS:-100}" --max-requests "${ASSESSMENT_MAX_REQUESTS:-1500}" --max-duration "$MAX_DURATION_SECONDS" --max-response-mb "${ASSESSMENT_MAX_RESPONSE_MB:-512}" --resume; then scanner_rc=0; else scanner_rc=$?; fi
  fi
  if ((COLLECTION_CANCELLED == 0 && COLLECTION_TIMED_OUT == 0)); then
    if run_bounded artifact-validation 300 "$out/artifact-smoke.log" "$PYTHON_BIN" "$VALIDATOR" "$out"; then validator_rc=0; else validator_rc=$?; fi
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

dashboard_cmdline(){
  local dashboard_pid="$1"
  [[ "$dashboard_pid" =~ ^[0-9]+$ ]] || return 1
  if [[ -r "/proc/$dashboard_pid/cmdline" ]]; then
    tr '\0' ' ' < "/proc/$dashboard_pid/cmdline"
  else
    ps -p "$dashboard_pid" -o args= 2>/dev/null
  fi
}

dashboard_process_matches(){
  local dashboard_pid="$1" command_line
  kill -0 "$dashboard_pid" 2>/dev/null || return 1
  command_line="$(dashboard_cmdline "$dashboard_pid" 2>/dev/null || true)"
  [[ "$command_line" == *"$TOOL_ROOT/src/assessment_dashboard.py"* ]] || return 1
  [[ "$command_line" == *"--static $TOOL_ROOT/web/public"* ]]
}

dashboard_process_is_assessment(){
  local dashboard_pid="$1" command_line
  kill -0 "$dashboard_pid" 2>/dev/null || return 1
  command_line="$(dashboard_cmdline "$dashboard_pid" 2>/dev/null || true)"
  [[ "$command_line" == *"assessment_dashboard.py"* ]]
}

dashboard_ready(){
  local response
  response="$(curl -fsS --max-time 2 -o /dev/null -w '%{http_code} %{content_type}' "http://127.0.0.1:$PORT/styles.css" 2>/dev/null)" || return 1
  [[ "$response" == "200 text/css"* ]]
}

stop_dashboard_process(){
  local dashboard_pid="$1" attempt
  kill -TERM "$dashboard_pid" 2>/dev/null || true
  for ((attempt=0; attempt<30; attempt++)); do
    kill -0 "$dashboard_pid" 2>/dev/null || return 0
    sleep 0.1
  done
  kill -KILL "$dashboard_pid" 2>/dev/null || true
}
web(){
  local pid="$OUTROOT/dashboard-$PORT.pid" log="$OUTROOT/dashboard-$PORT.log" host dashboard_pid="" attempt
  [[ -n "$PYTHON_BIN" ]] || select_python || { echo "ERRO: Python 3.10+ ausente" >&2; return 1; }
  need curl
  [[ -r "$TOOL_ROOT/web/public/styles.css" ]] || { echo "ERRO: CSS do dashboard ausente em $TOOL_ROOT/web/public/styles.css" >&2; return 1; }
  [[ -r "$pid" ]] && dashboard_pid="$(cat "$pid" 2>/dev/null || true)"

  if dashboard_process_matches "$dashboard_pid" && dashboard_ready; then
    echo "Dashboard ativo (PID $dashboard_pid)."
  else
    if dashboard_process_is_assessment "$dashboard_pid"; then
      echo "Dashboard obsoleto ou sem CSS (PID $dashboard_pid); reiniciando..."
      stop_dashboard_process "$dashboard_pid"
    elif [[ -n "$dashboard_pid" ]] && kill -0 "$dashboard_pid" 2>/dev/null; then
      echo "Aviso: PID $dashboard_pid nao pertence ao assessment; ele nao sera encerrado." >&2
    fi
    rm -f "$pid"
    nohup setsid "$PYTHON_BIN" "$TOOL_ROOT/src/assessment_dashboard.py" --root "$OUTROOT" --static "$TOOL_ROOT/web/public" --host 0.0.0.0 --port "$PORT" < /dev/null > "$log" 2>&1 &
    dashboard_pid=$!; echo "$dashboard_pid" > "$pid"
    for ((attempt=0; attempt<30; attempt++)); do
      if dashboard_process_matches "$dashboard_pid" && dashboard_ready; then break; fi
      sleep 0.1
    done
    if ! dashboard_process_matches "$dashboard_pid" || ! dashboard_ready; then
      echo "ERRO: dashboard nao iniciou; consulte $log" >&2
      stop_dashboard_process "$dashboard_pid"; rm -f "$pid"
      return 1
    fi
    echo "Dashboard iniciado (PID $dashboard_pid)."
  fi
  WEB_MANAGED_BY_MENU=1
  host="$(hostname -I | awk '{print $1}')"
  echo "Abra: http://$host:$PORT"
  echo "Log: $log"
}

need kubectl; need jq; need curl; need timeout; need setsid
select_python || { echo "ERRO: Python 3.10+ ausente; defina PYTHON_BIN se necessário" >&2; exit 1; }
[[ -r "$PREFLIGHT" ]] || { echo "ERRO: preflight ausente em $PREFLIGHT" >&2; exit 1; }
mkdir -p "$OUTROOT"
while :; do
  cat <<EOF

=== KUBERNETES / EKS ASSESSMENT (READ-ONLY | LIMITE ${MAX_DURATION_SECONDS}s) ===
1) Coleta ANTES do deploy
2) Coleta DEPOIS do deploy
3) Comparar coletas
4) Dashboard no terminal
5) Publicar dashboard web (porta $PORT)
6) Validar ambiente (preflight)
0) Sair
EOF
  read -r -p 'Opção: ' op
  case "$op" in
    1) collect before;; 2) collect after;; 3) compare;; 4) terminal;;
    5) web;; 6) run_preflight "${PROMETHEUS_URL:-}" "${EKS_CLUSTER_NAME:-}";;
    0) exit 0;; *) echo 'Opção inválida.';;
  esac
  [[ "$op" == 0 ]] || read -r -p 'Enter para continuar…' _
done
