#!/usr/bin/env bash
# Read-only Kubernetes assessment operator console and headless CLI.
set -euo pipefail

TOOL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTROOT="${ASSESSMENT_ROOT:-${XDG_STATE_HOME:-$PWD}/eks-assessment}"
ASSESS="$TOOL_ROOT/src/assess-eks.sh"
DISCOVERY="$TOOL_ROOT/src/eks-cluster-discovery.sh"
TELEMETRY="$TOOL_ROOT/src/prometheus_telemetry.py"
SCANNER="$TOOL_ROOT/src/eks_comprehensive_assessment.py"
VALIDATOR="$TOOL_ROOT/src/validate_assessment_artifacts.py"
CONTRACT_VALIDATOR="$TOOL_ROOT/src/assessment_contracts.py"
COLLECTOR_MANAGER="$TOOL_ROOT/src/collector_registry.py"
COLLECTOR_REGISTRY="$TOOL_ROOT/data/collectors.json"
BUNDLE_MANAGER="$TOOL_ROOT/src/collection_bundle.py"
NODE_EVIDENCE="$TOOL_ROOT/src/node_process_evidence.py"
CONFIGURATION_METADATA="$TOOL_ROOT/src/configuration_metadata.py"
BLUE_GREEN_READINESS="$TOOL_ROOT/src/blue_green_readiness.py"
MIGRATION_COMPARE="$TOOL_ROOT/src/migration_compare.py"
RELEASE_VERIFIER="$TOOL_ROOT/src/release_verification.py"
PROVIDER_VALIDATOR="$TOOL_ROOT/src/provider_validation.py"
REGRESSION_VALIDATOR="$TOOL_ROOT/src/regression_validation.py"
REGRESSION_POLICY="${ASSESSMENT_POLICY_FILE:-$TOOL_ROOT/data/assessment-policy.json}"
PREFLIGHT="$TOOL_ROOT/src/assessment-preflight.sh"
PYTHON_BIN="${PYTHON_BIN:-}"
PORT="${DASHBOARD_PORT:-8765}"
MAX_DURATION_SECONDS="${ASSESSMENT_MAX_DURATION_SECONDS:-1800}"
ACTIVE_PID=""
ACTIVE_COMPONENT=""
COLLECTION_CANCELLED=0
COLLECTION_TIMED_OUT=0
COLLECTION_STARTED_EPOCH=0
DASHBOARD_FOREGROUND=0
COMMAND="menu"
NON_INTERACTIVE=0
CLI_PHASE=""
CLI_CHANGE_ID=""
CLI_NAMESPACE="${ASSESSMENT_NAMESPACE:-}"
CLI_PROMETHEUS_URL="${PROMETHEUS_URL:-}"
CLI_PROMETHEUS_MODE="disabled"
CLI_PROMETHEUS_WINDOW="${PROMETHEUS_WINDOW:-7d}"
CLI_COLLECTION=""
CLI_BEFORE=""
CLI_AFTER=""
CLI_PROVIDER=""
CLI_PROFILE=""
CLI_ACTION=""
CLI_OUTPUT=""
CLI_BUNDLE=""
CLI_OLDER_THAN=""
CLI_CONFIRM=0
CLI_INCLUDE_BASELINES=0
CLI_RESUME=""
CLI_RETRY_FAILED=0
CLI_CHECKSUM=""
CLI_SBOM=""
CLI_PROVENANCE=""
CLI_SIGSTORE_BUNDLE=""
CLI_CERTIFICATE_IDENTITY=""
CLI_OIDC_ISSUER=""
CLI_REQUIRE_SIGNATURE=0
CLI_CONFIGMAP_METADATA="${ASSESSMENT_INCLUDE_CONFIGMAP_METADATA:-0}"
CLI_SECRET_METADATA="${ASSESSMENT_INCLUDE_SECRET_METADATA:-0}"
CLI_SOURCE=""
CLI_TARGET=""
CLI_MAPPING=""
declare -a CLI_INCLUDE_COLLECTORS=()
declare -a CLI_EXCLUDE_COLLECTORS=()
declare -a CLI_PROBE_URLS=()
[[ -z "$CLI_PROMETHEUS_URL" ]] || CLI_PROMETHEUS_MODE="explicit"

if [[ -t 1 && "${TERM:-dumb}" != dumb && -z "${NO_COLOR:-}" ]]; then
  C_RESET=$'\033[0m'; C_BOLD=$'\033[1m'; C_DIM=$'\033[2m'
  C_BLUE=$'\033[38;5;33m'; C_LIGHT=$'\033[38;5;75m'
  C_CYAN=$'\033[38;5;81m'; C_GREEN=$'\033[38;5;78m'; C_WHITE=$'\033[97m'; C_RED=$'\033[38;5;203m'
else
  C_RESET=''; C_BOLD=''; C_DIM=''; C_BLUE=''; C_LIGHT=''; C_CYAN=''; C_GREEN=''; C_WHITE=''; C_RED=''
fi

usage(){
  cat <<'EOF'
Uso:
  eks-assessment.sh
  eks-assessment.sh preflight [--namespace NAMESPACE] [--prometheus-url URL]
  eks-assessment.sh collect --phase before|after --change-id ID [opções]
  eks-assessment.sh list [--root DIRETÓRIO]
  eks-assessment.sh compare --before ID --after ID
  eks-assessment.sh terminal --collection ID
  eks-assessment.sh dashboard [--port PORTA] [--root DIRETÓRIO]
  eks-assessment.sh release-gate --collection ID --provider generic-kubernetes
  eks-assessment.sh regression-gate --before ID --after ID [--profile standard]
  eks-assessment.sh blue-green-gate --collection ID [--probe-url URL]
  eks-assessment.sh migration-gate --source ID --target ID [--mapping mapping.json]
  eks-assessment.sh validate --collection ID
  eks-assessment.sh bundle export --collection ID --output ARQUIVO.tar.gz
  eks-assessment.sh bundle verify --bundle ARQUIVO.tar.gz
  eks-assessment.sh bundle import --bundle ARQUIVO.tar.gz [--root DIRETÓRIO]
  eks-assessment.sh prune --older-than 30d [--confirm] [--include-baselines]
  eks-assessment.sh verify-release --archive PACOTE --checksum SHA256 [opções]
  eks-assessment.sh --help | --version

Opções de coleta:
  --namespace NAMESPACE          limita a coleta a um namespace
  --prometheus-url URL           consulta uma URL explícita, sem credenciais
  --no-prometheus                desabilita Prometheus de forma determinística
  --auto-detect-prometheus       permite somente a descoberta read-only existente
  --prometheus-window JANELA     1d, 3d, 7d, 14d ou 30d (padrão: 7d)
  --root DIRETÓRIO               diretório de coletas
  --max-duration SEGUNDOS        orçamento total entre 60 e 7200 segundos
  --include-collector ID[,ID]    executa required collectors e os IDs informados
  --exclude-collector ID[,ID]    omite collector opcional
  --resume ID                    retoma uma coleta parcial existente
  --retry-failed                 repete collectors que terminaram em FAIL
  --configmap-metadata           opt-in namespaced: persiste somente nome/keys de ConfigMaps
  --secret-metadata              opt-in namespaced: persiste somente nome/type/keys de Secrets
  --probe-url URL                probe HTTP/HTTPS read-only; repetível, sem query/credenciais

Sem subcomando, abre o menu interativo. Os subcomandos nunca solicitam input.
Variáveis principais: KUBECONFIG, ASSESSMENT_ROOT, ASSESSMENT_NAMESPACE,
PROMETHEUS_URL, EKS_CLUSTER_NAME e ASSESSMENT_MAX_DURATION_SECONDS.
EOF
}

require_cli_value(){
  if (($# < 2)) || [[ -z "${2:-}" ]]; then
    echo "ERRO: $1 exige um valor." >&2
    exit 2
  fi
}

case "${1:-}" in
  -h|--help) usage; exit 0 ;;
  --version) tr -d '[:space:]' < "$TOOL_ROOT/VERSION"; printf '\n'; exit 0 ;;
  menu) COMMAND="menu"; shift ;;
  preflight|collect|list|compare|terminal|dashboard|release-gate|regression-gate|blue-green-gate|migration-gate|validate|prune|verify-release)
    COMMAND="$1"; NON_INTERACTIVE=1; shift ;;
  bundle)
    COMMAND="bundle"; NON_INTERACTIVE=1; shift
    CLI_ACTION="${1:-}"; [[ -z "$CLI_ACTION" ]] || shift
    ;;
  "") ;;
  *) echo "Comando desconhecido: $1" >&2; usage >&2; exit 2 ;;
esac

while (($#)); do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --phase) require_cli_value "$@"; CLI_PHASE="$2"; shift 2 ;;
    --change-id) require_cli_value "$@"; CLI_CHANGE_ID="$2"; shift 2 ;;
    --namespace) require_cli_value "$@"; CLI_NAMESPACE="$2"; shift 2 ;;
    --prometheus-url) require_cli_value "$@"; CLI_PROMETHEUS_URL="$2"; CLI_PROMETHEUS_MODE="explicit"; shift 2 ;;
    --no-prometheus) CLI_PROMETHEUS_URL=""; CLI_PROMETHEUS_MODE="disabled"; shift ;;
    --auto-detect-prometheus) CLI_PROMETHEUS_URL=""; CLI_PROMETHEUS_MODE="auto"; shift ;;
    --prometheus-window) require_cli_value "$@"; CLI_PROMETHEUS_WINDOW="$2"; shift 2 ;;
    --collection) require_cli_value "$@"; CLI_COLLECTION="$2"; shift 2 ;;
    --before) require_cli_value "$@"; CLI_BEFORE="$2"; shift 2 ;;
    --after) require_cli_value "$@"; CLI_AFTER="$2"; shift 2 ;;
    --source) require_cli_value "$@"; CLI_SOURCE="$2"; shift 2 ;;
    --target) require_cli_value "$@"; CLI_TARGET="$2"; shift 2 ;;
    --mapping) require_cli_value "$@"; CLI_MAPPING="$2"; shift 2 ;;
    --provider) require_cli_value "$@"; CLI_PROVIDER="$2"; shift 2 ;;
    --profile) require_cli_value "$@"; CLI_PROFILE="$2"; shift 2 ;;
    --output) require_cli_value "$@"; CLI_OUTPUT="$2"; shift 2 ;;
    --bundle|--archive) require_cli_value "$@"; CLI_BUNDLE="$2"; shift 2 ;;
    --older-than) require_cli_value "$@"; CLI_OLDER_THAN="$2"; shift 2 ;;
    --confirm) CLI_CONFIRM=1; shift ;;
    --include-baselines) CLI_INCLUDE_BASELINES=1; shift ;;
    --include-collector) require_cli_value "$@"; CLI_INCLUDE_COLLECTORS+=("$2"); shift 2 ;;
    --exclude-collector) require_cli_value "$@"; CLI_EXCLUDE_COLLECTORS+=("$2"); shift 2 ;;
    --resume) require_cli_value "$@"; CLI_RESUME="$2"; shift 2 ;;
    --retry-failed) CLI_RETRY_FAILED=1; shift ;;
    --configmap-metadata) CLI_CONFIGMAP_METADATA=1; shift ;;
    --secret-metadata) CLI_SECRET_METADATA=1; shift ;;
    --probe-url) require_cli_value "$@"; CLI_PROBE_URLS+=("$2"); shift 2 ;;
    --checksum) require_cli_value "$@"; CLI_CHECKSUM="$2"; shift 2 ;;
    --sbom) require_cli_value "$@"; CLI_SBOM="$2"; shift 2 ;;
    --provenance) require_cli_value "$@"; CLI_PROVENANCE="$2"; shift 2 ;;
    --sigstore-bundle) require_cli_value "$@"; CLI_SIGSTORE_BUNDLE="$2"; shift 2 ;;
    --certificate-identity) require_cli_value "$@"; CLI_CERTIFICATE_IDENTITY="$2"; shift 2 ;;
    --oidc-issuer) require_cli_value "$@"; CLI_OIDC_ISSUER="$2"; shift 2 ;;
    --require-signature) CLI_REQUIRE_SIGNATURE=1; shift ;;
    --root) require_cli_value "$@"; OUTROOT="$2"; shift 2 ;;
    --port) require_cli_value "$@"; PORT="$2"; shift 2 ;;
    --max-duration) require_cli_value "$@"; MAX_DURATION_SECONDS="$2"; shift 2 ;;
    *) echo "Opção desconhecida para $COMMAND: $1" >&2; usage >&2; exit 2 ;;
  esac
done

valid_collection_id(){ [[ "$1" =~ ^[A-Za-z0-9._-]+$ && "$1" != "." && "$1" != ".." ]]; }
valid_namespace(){ [[ -z "$1" || "$1" =~ ^[a-z0-9]([-a-z0-9.]*[a-z0-9])?$ ]]; }

if ((NON_INTERACTIVE == 1)); then
  [[ "$CLI_CONFIGMAP_METADATA" =~ ^(0|1)$ ]] || { echo "ERRO: ASSESSMENT_INCLUDE_CONFIGMAP_METADATA deve ser 0 ou 1." >&2; exit 2; }
  [[ "$CLI_SECRET_METADATA" =~ ^(0|1)$ ]] || { echo "ERRO: ASSESSMENT_INCLUDE_SECRET_METADATA deve ser 0 ou 1." >&2; exit 2; }
  case "$COMMAND" in
    collect)
      if [[ -n "$CLI_RESUME" ]]; then
        valid_collection_id "$CLI_RESUME" || { echo "ERRO: --resume exige um ID de coleta válido." >&2; exit 2; }
      else
        [[ "$CLI_PHASE" == before || "$CLI_PHASE" == after ]] || { echo "ERRO: collect exige --phase before|after." >&2; exit 2; }
        if [[ -z "$CLI_CHANGE_ID" ]] || ! valid_collection_id "$CLI_CHANGE_ID"; then
          echo "ERRO: collect exige --change-id com caracteres [A-Za-z0-9._-]." >&2
          exit 2
        fi
      fi
      valid_namespace "$CLI_NAMESPACE" || { echo "ERRO: namespace inválido." >&2; exit 2; }
      if [[ -z "$CLI_RESUME" && -z "$CLI_NAMESPACE" ]] && ((CLI_CONFIGMAP_METADATA == 1 || CLI_SECRET_METADATA == 1)); then
        echo "ERRO: --configmap-metadata e --secret-metadata exigem --namespace explícito." >&2
        exit 2
      fi
      [[ "$CLI_PROMETHEUS_WINDOW" =~ ^(1d|3d|7d|14d|30d)$ ]] || { echo "ERRO: janela Prometheus inválida." >&2; exit 2; }
      ;;
    preflight)
      valid_namespace "$CLI_NAMESPACE" || { echo "ERRO: namespace inválido." >&2; exit 2; }
      if [[ -z "$CLI_NAMESPACE" ]] && ((CLI_CONFIGMAP_METADATA == 1 || CLI_SECRET_METADATA == 1)); then
        echo "ERRO: --configmap-metadata e --secret-metadata exigem --namespace explícito." >&2
        exit 2
      fi
      ;;
    compare|regression-gate)
      if ! valid_collection_id "$CLI_BEFORE" || ! valid_collection_id "$CLI_AFTER"; then
        echo "ERRO: $COMMAND exige --before e --after válidos." >&2
        exit 2
      fi
      [[ "$CLI_BEFORE" != "$CLI_AFTER" ]] || { echo "ERRO: as coletas devem ser diferentes." >&2; exit 2; }
      ;;
    terminal) valid_collection_id "$CLI_COLLECTION" || { echo "ERRO: terminal exige --collection válido." >&2; exit 2; } ;;
    release-gate)
      valid_collection_id "$CLI_COLLECTION" || { echo "ERRO: release-gate exige --collection válido." >&2; exit 2; }
      [[ "$CLI_PROVIDER" =~ ^(eks|aks|gke|generic-kubernetes)$ ]] || { echo "ERRO: provider inválido." >&2; exit 2; }
      ;;
    blue-green-gate)
      valid_collection_id "$CLI_COLLECTION" || { echo "ERRO: blue-green-gate exige --collection válido." >&2; exit 2; }
      ;;
    migration-gate)
      if ! valid_collection_id "$CLI_SOURCE" || ! valid_collection_id "$CLI_TARGET"; then
        echo "ERRO: migration-gate exige --source e --target válidos." >&2
        exit 2
      fi
      [[ "$CLI_SOURCE" != "$CLI_TARGET" ]] || { echo "ERRO: source e target devem ser diferentes." >&2; exit 2; }
      [[ -z "$CLI_MAPPING" || -r "$CLI_MAPPING" ]] || { echo "ERRO: mapping não encontrado." >&2; exit 2; }
      ;;
    validate)
      valid_collection_id "$CLI_COLLECTION" || { echo "ERRO: validate exige --collection válido." >&2; exit 2; }
      ;;
    bundle)
      [[ "$CLI_ACTION" =~ ^(export|verify|import)$ ]] || { echo "ERRO: bundle exige export, verify ou import." >&2; exit 2; }
      if [[ "$CLI_ACTION" == export ]]; then
        valid_collection_id "$CLI_COLLECTION" || { echo "ERRO: bundle export exige --collection válido." >&2; exit 2; }
        [[ -n "$CLI_OUTPUT" ]] || { echo "ERRO: bundle export exige --output." >&2; exit 2; }
      else
        [[ -n "$CLI_BUNDLE" ]] || { echo "ERRO: bundle $CLI_ACTION exige --bundle." >&2; exit 2; }
      fi
      ;;
    prune)
      [[ "$CLI_OLDER_THAN" =~ ^[1-9][0-9]*[hd]$ ]] || { echo "ERRO: prune exige --older-than Nd|Nh." >&2; exit 2; }
      ;;
    verify-release)
      [[ -n "$CLI_BUNDLE" && -n "$CLI_CHECKSUM" ]] || { echo "ERRO: verify-release exige --archive e --checksum." >&2; exit 2; }
      ;;
    dashboard)
      if [[ ! "$PORT" =~ ^[0-9]+$ ]] || ((PORT < 1 || PORT > 65535)); then
        echo "ERRO: porta inválida." >&2
        exit 2
      fi
      ;;
  esac
fi

if [[ ! "$MAX_DURATION_SECONDS" =~ ^[0-9]+$ ]]; then
  ((NON_INTERACTIVE == 0)) || { echo "ERRO: --max-duration deve ser numérico." >&2; exit 2; }
  MAX_DURATION_SECONDS=1800
fi
if ((NON_INTERACTIVE == 1 && (MAX_DURATION_SECONDS < 60 || MAX_DURATION_SECONDS > 7200))); then
  echo "ERRO: --max-duration deve estar entre 60 e 7200 segundos." >&2
  exit 2
fi
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
  if ((DASHBOARD_FOREGROUND == 1)); then
    return 0
  fi
  COLLECTION_CANCELLED=1
  echo >&2
  echo "Cancelamento solicitado; preservando a coleta parcial como CANCELLED." >&2
  terminate_active
}

cleanup_menu(){
  terminate_active
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
  PYTHON_BIN="$PYTHON_BIN" PROMETHEUS_URL="${1:-}" EKS_CLUSTER_NAME="${2:-${EKS_CLUSTER_NAME:-}}" ASSESSMENT_NAMESPACE="${3:-}" \
    ASSESSMENT_INCLUDE_CONFIGMAP_METADATA="$CLI_CONFIGMAP_METADATA" ASSESSMENT_INCLUDE_SECRET_METADATA="$CLI_SECRET_METADATA" bash "$PREFLIGHT"
}
suggest_prometheus_url(){
  kubectl get services --all-namespaces -o json --request-timeout=10s 2>/dev/null | jq -r '
    [.items[]
      | select(.spec.clusterIP != null and .spec.clusterIP != "None")
      | . as $service
      | .spec.ports[]?
      | select((.port == 9090) or ((.name // "") | test("prometheus|web|http"; "i")))
      | select((($service.metadata.name // "") | test("prometheus"; "i")) or (($service.metadata.labels["app.kubernetes.io/name"] // "") | test("prometheus"; "i")))
      | {score: (if .port == 9090 then 0 else 1 end), url: ("http://" + $service.spec.clusterIP + ":" + (.port | tostring))}]
    | sort_by(.score, .url)
    | first.url // empty'
}
collections(){ find "$OUTROOT" -mindepth 1 -maxdepth 1 -type d -name 'eks-*' -printf '%f\n' 2>/dev/null | sort; }

render_menu(){
  local context version total
  context="$(kubectl config current-context 2>/dev/null || echo indisponível)"
  version="$(tr -d '[:space:]' < "$TOOL_ROOT/VERSION")"
  total="$(collections | wc -l | tr -d ' ')"
  if [[ -t 1 && "${ASSESSMENT_MENU_CLEAR:-1}" == 1 ]]; then printf '\033[2J\033[H'; fi
  menu_row(){
    local color="$1" text="$2" padding=$((66 - ${#2}))
    ((padding < 0)) && padding=0
    printf '%s│%s  %s%*s  %s│%s\n' "$C_BLUE" "$color" "$text" "$padding" '' "$C_BLUE" "$C_RESET"
  }
  printf '\n%s╭──────────────────────────────────────────────────────────────────────╮%s\n' "$C_BLUE" "$C_RESET"
  menu_row "$C_WHITE$C_BOLD" "☸  KUBERNETES ASSESSMENT CONSOLE"
  menu_row "$C_GREEN$C_BOLD" "READ-ONLY  ·  versão $version  ·  limite ${MAX_DURATION_SECONDS}s"
  printf '%s├──────────────────────────────────────────────────────────────────────┤%s\n' "$C_BLUE" "$C_RESET"
  menu_row "$C_RESET" "Contexto: ${context:0:56}"
  menu_row "$C_RESET" "Coletas: $total  ·  Dashboard web: porta $PORT"
  printf '%s├──────────────────────────────────────────────────────────────────────┤%s\n' "$C_BLUE" "$C_RESET"
  menu_row "$C_LIGHT$C_BOLD" "DATA COLLECTION"
  menu_row "$C_CYAN" "[1] Coleta ANTES do deploy        [2] Coleta DEPOIS do deploy"
  menu_row "$C_CYAN" "[6] Validar ambiente (preflight)"
  menu_row "$C_RESET" ""
  menu_row "$C_LIGHT$C_BOLD" "INSIGHTS & REPORTING"
  menu_row "$C_CYAN" "[3] Comparar coletas              [4] Dashboard no terminal"
  menu_row "$C_CYAN" "[5] Abrir dashboard web nesta sessão (porta $PORT)"
  menu_row "$C_RESET" ""
  menu_row "$C_LIGHT$C_BOLD" "GOVERNANÇA & RELEASE"
  menu_row "$C_CYAN" "[7] Release Gate por provider   [8] Regression Gate entre coletas"
  menu_row "$C_CYAN" "[9] Validar contratos JSON       [10] Exportar bundle portátil"
  menu_row "$C_CYAN" "[11] Blue-Green Readiness         [12] Migration Gate source → target"
  menu_row "$C_RED" "[0] Sair"
  printf '%s╰──────────────────────────────────────────────────────────────────────╯%s\n' "$C_BLUE" "$C_RESET"
}

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
  local status="${9:-$([[ "$completed" == true ]] && echo COMPLETED || echo FAILED)}" reason="${10:-}" max_duration="${11:-$MAX_DURATION_SECONDS}" namespace_scope="${12:-*}" created duration=0 metadata_tmp
  if ((COLLECTION_STARTED_EPOCH > 0)); then duration=$(( $(date +%s) - COLLECTION_STARTED_EPOCH )); fi
  created="$(jq -r '.createdAt // empty' "$out/metadata.json" 2>/dev/null || true)"; created="${created:-$(date -u +%FT%TZ)}"
  [[ -r "$out/metadata.json" ]] || printf '%s\n' '{}' > "$out/metadata.json"
  metadata_tmp="$out/.metadata.json.tmp"
  jq --arg id "$id" --arg phase "$phase" --arg created "$created" --arg finished "$(date -u +%FT%TZ)" \
    --arg cluster "$cluster_name" --arg context "$cluster_context" --arg status "$status" --arg reason "$reason" \
    --arg namespaceScope "$namespace_scope" \
    --argjson baseline "$baseline" --argjson completed "$completed" --argjson codes "$codes" --argjson maxDuration "$max_duration" --argjson duration "$duration" \
    '. + {id:$id,phase:$phase,createdAt:$created,finishedAt:(if $status=="RUNNING" then null else $finished end),clusterName:$cluster,context:$context,namespaceScope:(if $namespaceScope=="" then "*" else $namespaceScope end),baseline:$baseline,status:$status,completed:$completed,cancelled:($status=="CANCELLED"),cancelReason:(if $reason=="" then null else $reason end),maxDurationSeconds:$maxDuration,readOnly:true,collectorExitCodes:$codes,performance:((.performance // {}) + {durationSeconds:$duration})}' \
    "$out/metadata.json" > "$metadata_tmp"
  mv "$metadata_tmp" "$out/metadata.json"
  if [[ -r "$out/collector-state.json" ]]; then
    jq --slurpfile state "$out/collector-state.json" \
      '. + {collectorProgress:{progressPercent:($state[0].progressPercent // 0),plan:($state[0].plan // []),collectors:($state[0].collectors // {})}}' \
      "$out/metadata.json" > "$metadata_tmp"
    mv "$metadata_tmp" "$out/metadata.json"
  fi
  cp "$out/metadata.json" "$out/menu-metadata.json"
}

collector_init(){
  local out="$1" prometheus_state="$2" configuration_state="$3" resume_flag="$4" argument
  local -a args=("$PYTHON_BIN" "$COLLECTOR_MANAGER" --registry "$COLLECTOR_REGISTRY" init --collection "$out" --channel cli --prometheus "$prometheus_state" --configuration-metadata "$configuration_state")
  for argument in "${CLI_INCLUDE_COLLECTORS[@]}"; do args+=(--include "$argument"); done
  for argument in "${CLI_EXCLUDE_COLLECTORS[@]}"; do args+=(--exclude "$argument"); done
  [[ "$resume_flag" == true ]] && args+=(--resume)
  ((CLI_RETRY_FAILED == 1)) && args+=(--retry-failed)
  "${args[@]}" >/dev/null
}

collector_validate_plan(){
  local prometheus_state="$1" configuration_state="$2" argument
  local -a args=("$PYTHON_BIN" "$COLLECTOR_MANAGER" --registry "$COLLECTOR_REGISTRY" plan --channel cli --prometheus "$prometheus_state" --configuration-metadata "$configuration_state")
  for argument in "${CLI_INCLUDE_COLLECTORS[@]}"; do args+=(--include "$argument"); done
  for argument in "${CLI_EXCLUDE_COLLECTORS[@]}"; do args+=(--exclude "$argument"); done
  "${args[@]}" >/dev/null
}

collector_enabled(){
  local out="$1" collector="$2"
  jq -e --arg id "$collector" '.plan | any(.id == $id)' "$out/collector-state.json" >/dev/null
}

collector_should_run(){
  local out="$1" collector="$2"; shift 2
  local -a args=("$PYTHON_BIN" "$COLLECTOR_MANAGER" --registry "$COLLECTOR_REGISTRY" should-run --collection "$out" --collector "$collector")
  ((CLI_RETRY_FAILED == 1)) && args+=(--retry-failed)
  "${args[@]}" >/dev/null 2>&1
}

collector_mark(){
  local out="$1" collector="$2" state="$3" exit_code="${4:-}" detail="${5:-}"
  local -a args=("$PYTHON_BIN" "$COLLECTOR_MANAGER" --registry "$COLLECTOR_REGISTRY" mark --collection "$out" --collector "$collector" --state "$state")
  [[ -z "$exit_code" ]] || args+=(--exit-code "$exit_code")
  [[ -z "$detail" ]] || args+=(--detail "$detail")
  "${args[@]}" >/dev/null
}

collector_finish(){
  local out="$1" collector="$2" rc="$3" state=PASS
  if ((COLLECTION_CANCELLED == 1)); then state=CANCELLED
  elif ((COLLECTION_TIMED_OUT == 1)); then state=TIMED_OUT
  elif ((rc != 0)); then state=FAIL
  fi
  collector_mark "$out" "$collector" "$state" "$rc"
}

collector_existing_rc(){
  local out="$1" collector="$2"
  jq -r --arg id "$collector" '.collectors[$id].exitCode // 0' "$out/collector-state.json"
}

validate_collection_contracts(){
  local id="$1" directory="$OUTROOT/$1"
  valid_collection_id "$id" || { echo 'ID de coleta inválido.' >&2; return 2; }
  [[ -d "$directory" ]] || { echo 'Coleta não encontrada.' >&2; return 2; }
  "$PYTHON_BIN" "$CONTRACT_VALIDATOR" --collection "$directory"
}

bundle_command(){
  local version directory
  version="$(tr -d '[:space:]' < "$TOOL_ROOT/VERSION")"
  case "$CLI_ACTION" in
    export)
      directory="$OUTROOT/$CLI_COLLECTION"
      [[ -d "$directory" ]] || { echo 'Coleta não encontrada.' >&2; return 2; }
      "$PYTHON_BIN" "$BUNDLE_MANAGER" export --collection "$directory" --output "$CLI_OUTPUT" --tool-version "$version"
      ;;
    verify) "$PYTHON_BIN" "$BUNDLE_MANAGER" verify --bundle "$CLI_BUNDLE" ;;
    import) "$PYTHON_BIN" "$BUNDLE_MANAGER" import --bundle "$CLI_BUNDLE" --root "$OUTROOT" ;;
  esac
}

prune_collections(){
  local -a args=("$PYTHON_BIN" "$BUNDLE_MANAGER" prune --root "$OUTROOT" --older-than "$CLI_OLDER_THAN")
  ((CLI_CONFIRM == 1)) && args+=(--confirm)
  ((CLI_INCLUDE_BASELINES == 1)) && args+=(--include-baselines)
  "${args[@]}"
}

verify_release(){
  local -a args=("$PYTHON_BIN" "$RELEASE_VERIFIER" --archive "$CLI_BUNDLE" --checksum "$CLI_CHECKSUM")
  [[ -z "$CLI_SBOM" ]] || args+=(--sbom "$CLI_SBOM")
  [[ -z "$CLI_PROVENANCE" ]] || args+=(--provenance "$CLI_PROVENANCE")
  [[ -z "$CLI_SIGSTORE_BUNDLE" ]] || args+=(--sigstore-bundle "$CLI_SIGSTORE_BUNDLE")
  [[ -z "$CLI_CERTIFICATE_IDENTITY" ]] || args+=(--certificate-identity "$CLI_CERTIFICATE_IDENTITY")
  [[ -z "$CLI_OIDC_ISSUER" ]] || args+=(--oidc-issuer "$CLI_OIDC_ISSUER")
  ((CLI_REQUIRE_SIGNATURE == 1)) && args+=(--require-signature)
  "${args[@]}"
}

interactive_validate(){
  local latest id
  latest="$(collections | tail -1)"
  [[ -n "$latest" ]] || { echo 'Nenhuma coleta disponível.'; return 0; }
  collections | nl -ba
  read -r -p "ID da coleta (Enter = $latest): " id
  validate_collection_contracts "${id:-$latest}"
}

interactive_bundle(){
  local latest id output version
  latest="$(collections | tail -1)"
  [[ -n "$latest" ]] || { echo 'Nenhuma coleta disponível.'; return 0; }
  collections | nl -ba
  read -r -p "ID da coleta (Enter = $latest): " id
  id="${id:-$latest}"
  valid_collection_id "$id" || { echo 'ID de coleta inválido.' >&2; return 1; }
  output="$OUTROOT/${id}.bundle.tar.gz"
  version="$(tr -d '[:space:]' < "$TOOL_ROOT/VERSION")"
  "$PYTHON_BIN" "$BUNDLE_MANAGER" export --collection "$OUTROOT/$id" --output "$output" --tool-version "$version"
}

collect(){
  local phase="$1" label id out prom_url prom_window answer cluster_context cluster_name eks_name status reason namespace
  local existing_context required_failures resume=false prometheus_state=disabled configuration_state=disabled probe_csv interactive_probe_url=""
  local preflight_rc=0 assess_rc=0 discovery_rc=0 configuration_rc=0 telemetry_rc=0 node_rc=0 scanner_rc=0 validator_rc=0 contract_rc=0 completed=false baseline=false codes
  local -a discovery_args scanner_args metadata_args
  COLLECTION_CANCELLED=0; COLLECTION_TIMED_OUT=0; COLLECTION_STARTED_EPOCH=0
  if ((NON_INTERACTIVE == 1)) && [[ -n "$CLI_RESUME" ]]; then
    resume=true
    id="$CLI_RESUME"
    out="$OUTROOT/$id"
    [[ -d "$out" && -r "$out/metadata.json" ]] || { echo "ERRO: coleta para retomada não encontrada: $id" >&2; return 2; }
    [[ "$(jq -r '.status // "UNKNOWN"' "$out/metadata.json")" != COMPLETED ]] || { echo "ERRO: a coleta $id já está concluída." >&2; return 2; }
    phase="$(jq -r '.phase // "manual"' "$out/metadata.json")"
    label="resume"
    namespace="$CLI_NAMESPACE"
    if [[ -z "$namespace" ]]; then namespace="$(jq -r '.namespaceScope // "*"' "$out/metadata.json")"; fi
    [[ "$namespace" == '*' ]] && namespace=""
    baseline="$(jq -r '.baseline // false' "$out/metadata.json")"
    prom_url="$CLI_PROMETHEUS_URL"
    prom_window="$CLI_PROMETHEUS_WINDOW"
  elif ((NON_INTERACTIVE == 1)); then
    label="$CLI_CHANGE_ID"
    namespace="$CLI_NAMESPACE"
    prom_url="$CLI_PROMETHEUS_URL"
    prom_window="$CLI_PROMETHEUS_WINDOW"
  else
    read -r -p "Identificador da mudança ($phase): " label
    label="${label:-manual}"
    namespace="${ASSESSMENT_NAMESPACE:-}"
    prom_url="${PROMETHEUS_URL:-}"
    prom_window="${PROMETHEUS_WINDOW:-7d}"
  fi
  label="${label//[^a-zA-Z0-9._-]/-}"
  if [[ -z "$prom_url" && ( "$NON_INTERACTIVE" == 0 || "$CLI_PROMETHEUS_MODE" == auto ) ]]; then
    prom_url="$(suggest_prometheus_url || true)"
    [[ -z "$prom_url" ]] || echo "Prometheus detectado como sugestão read-only: $prom_url"
  fi
  if ((NON_INTERACTIVE == 0)); then
    read -r -p "Namespace (Enter = ${namespace:-cluster inteiro}): " answer
    namespace="${answer:-$namespace}"
    read -r -p "Validar nomes e keys de ConfigMaps? [s/N]: " answer
    if [[ "$answer" =~ ^[sSyY]$ ]]; then CLI_CONFIGMAP_METADATA=1; else CLI_CONFIGMAP_METADATA=0; fi
    echo "AVISO: Secret metadata exige get/list; valores não serão persistidos, mas passam pela memória do processo."
    read -r -p "Validar nomes, types e keys de Secrets? [s/N]: " answer
    if [[ "$answer" =~ ^[sSyY]$ ]]; then CLI_SECRET_METADATA=1; else CLI_SECRET_METADATA=0; fi
    read -r -p "URL HTTP/HTTPS para probe de cutover (Enter = nenhuma): " interactive_probe_url
  fi
  [[ -z "$namespace" || "$namespace" =~ ^[a-z0-9]([-a-z0-9.]*[a-z0-9])?$ ]] || { echo "Namespace inválido." >&2; return 1; }
  if [[ -z "$namespace" ]] && ((CLI_CONFIGMAP_METADATA == 1 || CLI_SECRET_METADATA == 1)); then
    echo "ERRO: ConfigMap/Secret metadata exige namespace explícito; coleta cluster-wide não é permitida para esse opt-in." >&2
    return 2
  fi
  if ((NON_INTERACTIVE == 0)); then
    read -r -p "URL explícita do Prometheus (Enter = ${prom_url:-DISABLED}): " answer
    prom_url="${answer:-$prom_url}"
    read -r -p "Janela Prometheus 1d/3d/7d/14d/30d [${prom_window}]: " answer
    prom_window="${answer:-$prom_window}"
  fi
  [[ "$prom_window" =~ ^(1d|3d|7d|14d|30d)$ ]] || prom_window=7d
  [[ -z "$prom_url" ]] || prometheus_state=enabled
  if ((CLI_CONFIGMAP_METADATA == 1 || CLI_SECRET_METADATA == 1)); then configuration_state=enabled; fi
  collector_validate_plan "$prometheus_state" "$configuration_state" || return $?

  IFS=$'\t' read -r cluster_context cluster_name eks_name < <(cluster_identity)
  if [[ "$resume" == true ]]; then
    existing_context="$(jq -r '.context // empty' "$out/metadata.json")"
    if [[ -n "$existing_context" && "$existing_context" != "$cluster_context" ]]; then
      echo "ERRO: a coleta pertence ao contexto $existing_context, mas o contexto atual é $cluster_context." >&2
      return 2
    fi
  fi
  if ! run_preflight "$prom_url" "$eks_name" "$namespace"; then
    echo "Coleta não iniciada: corrija os itens FAIL do preflight." >&2
    return 1
  fi
  COLLECTION_STARTED_EPOCH="$(date +%s)"
  if [[ "$resume" != true ]]; then
    mkdir -p "$OUTROOT"
    out="$(mktemp -d "$OUTROOT/eks-$(date -u +%Y%m%dT%H%M%SZ)-${phase}-${label}.XXXXXXXX")"
    id="$(basename "$out")"
    [[ "$phase" == before ]] && baseline=true
  fi
  write_metadata "$out" "$id" "$phase" "$cluster_name" "$cluster_context" "$baseline" false '[]' RUNNING '' "$MAX_DURATION_SECONDS" "$namespace"
  collector_init "$out" "$prometheus_state" "$configuration_state" "$resume"
  collector_mark "$out" preflight PASS 0
  echo "== Coleta $phase: $id | cluster: $cluster_name | limite total: ${MAX_DURATION_SECONDS}s$( [[ "$resume" == true ]] && printf ' | RETOMADA' ) =="
  echo "Ctrl+C cancela toda a árvore; dados parciais serão preservados."

  if collector_enabled "$out" assessment; then
    if collector_should_run "$out" assessment; then
      collector_mark "$out" assessment RUNNING
      if run_bounded assessment 600 "$out/assessment.log" env OUTPUT_DIR="$out" EKS_CLUSTER_NAME="$eks_name" PYTHON_BIN="$PYTHON_BIN" ASSESSMENT_NAMESPACE="$namespace" ASSESSMENT_MAX_DURATION_SECONDS="$MAX_DURATION_SECONDS" bash "$ASSESS"; then assess_rc=0; else assess_rc=$?; fi
      collector_finish "$out" assessment "$assess_rc"
    else
      assess_rc="$(collector_existing_rc "$out" assessment)"; echo "Reutilizando collector assessment (exit code $assess_rc)."
    fi
  fi
  if ((COLLECTION_CANCELLED == 0 && COLLECTION_TIMED_OUT == 0)) && collector_enabled "$out" discovery; then
    if collector_should_run "$out" discovery; then
      collector_mark "$out" discovery RUNNING
    discovery_args=(--output-dir "$out/discovery" --combined-report)
    [[ -n "$namespace" ]] && discovery_args+=(--namespace "$namespace")
    if run_bounded discovery 900 "$out/discovery.log" bash "$DISCOVERY" "${discovery_args[@]}"; then discovery_rc=0; else discovery_rc=$?; fi
      collector_finish "$out" discovery "$discovery_rc"
    else
      discovery_rc="$(collector_existing_rc "$out" discovery)"; echo "Reutilizando collector discovery (exit code $discovery_rc)."
    fi
  fi
  if ((COLLECTION_CANCELLED == 0 && COLLECTION_TIMED_OUT == 0)) && collector_enabled "$out" configuration-metadata; then
    if collector_should_run "$out" configuration-metadata; then
      collector_mark "$out" configuration-metadata RUNNING
      metadata_args=(--output "$out/configuration-metadata.json")
      [[ -n "$namespace" ]] && metadata_args+=(--namespace "$namespace")
      ((CLI_CONFIGMAP_METADATA == 1)) && metadata_args+=(--include-configmaps)
      ((CLI_SECRET_METADATA == 1)) && metadata_args+=(--include-secrets)
      if run_bounded configuration-metadata 300 "$out/configuration-metadata.log" "$PYTHON_BIN" "$CONFIGURATION_METADATA" "${metadata_args[@]}"; then configuration_rc=0; else configuration_rc=$?; fi
      collector_finish "$out" configuration-metadata "$configuration_rc"
    else
      configuration_rc="$(collector_existing_rc "$out" configuration-metadata)"; echo "Reutilizando collector configuration-metadata (exit code $configuration_rc)."
    fi
  fi
  if ((COLLECTION_CANCELLED == 0 && COLLECTION_TIMED_OUT == 0)) && collector_enabled "$out" prometheus; then
    if collector_should_run "$out" prometheus; then
      collector_mark "$out" prometheus RUNNING
      if run_bounded_capture prometheus 1200 "$out/prometheus-telemetry.json" "$out/prometheus-telemetry.log" "$PYTHON_BIN" "$TELEMETRY" --url "$prom_url" --window "$prom_window" --workloads-file "$out/workloads.json"; then telemetry_rc=0; else telemetry_rc=$?; fi
      collector_finish "$out" prometheus "$telemetry_rc"
    else
      telemetry_rc="$(collector_existing_rc "$out" prometheus)"; echo "Reutilizando collector prometheus (exit code $telemetry_rc)."
    fi
  elif [[ ! -r "$out/prometheus-telemetry.json" ]]; then
    printf '%s\n' '{"state":"DISABLED","reason":"PROMETHEUS_URL not explicitly configured","workloads":[]}' > "$out/prometheus-telemetry.json"
  fi
  if ((COLLECTION_CANCELLED == 0 && COLLECTION_TIMED_OUT == 0)) && collector_enabled "$out" node-evidence; then
    if collector_should_run "$out" node-evidence; then
      collector_mark "$out" node-evidence RUNNING
      if run_bounded node-evidence 180 "$out/node-process-evidence.log" "$PYTHON_BIN" "$NODE_EVIDENCE" --url "$prom_url" --nodes-file "$out/nodes.json" --output "$out/node-process-evidence.json"; then node_rc=0; else node_rc=$?; fi
      collector_finish "$out" node-evidence "$node_rc"
    else
      node_rc="$(collector_existing_rc "$out" node-evidence)"; echo "Reutilizando collector node-evidence (exit code $node_rc)."
    fi
  fi
  if ((COLLECTION_CANCELLED == 0 && COLLECTION_TIMED_OUT == 0)) && collector_enabled "$out" comprehensive; then
    if collector_should_run "$out" comprehensive; then
      collector_mark "$out" comprehensive RUNNING
    scanner_args=(--snapshot-dir "$out" --collect-live --timeout 30 --chunk-size 200 --inventory-workers "${ASSESSMENT_WORKERS:-4}" --api-delay-ms "${ASSESSMENT_API_DELAY_MS:-100}" --max-requests "${ASSESSMENT_MAX_REQUESTS:-1500}" --max-duration "$MAX_DURATION_SECONDS" --max-response-mb "${ASSESSMENT_MAX_RESPONSE_MB:-512}")
    [[ -n "$namespace" ]] && scanner_args+=(--namespace "$namespace")
    [[ "$resume" == true ]] && scanner_args+=(--resume)
    probe_csv="${ASSESSMENT_PROBE_URLS:-}"
    if ((${#CLI_PROBE_URLS[@]})); then probe_csv="$(IFS=,; printf '%s' "${CLI_PROBE_URLS[*]}")"; fi
    if [[ -n "$interactive_probe_url" ]]; then probe_csv="${probe_csv:+$probe_csv,}$interactive_probe_url"; fi
    if run_bounded comprehensive "$MAX_DURATION_SECONDS" "$out/comprehensive-assessment.log" env ASSESSMENT_PROBE_URLS="$probe_csv" "$PYTHON_BIN" "$SCANNER" "${scanner_args[@]}"; then scanner_rc=0; else scanner_rc=$?; fi
      collector_finish "$out" comprehensive "$scanner_rc"
    else
      scanner_rc="$(collector_existing_rc "$out" comprehensive)"; echo "Reutilizando collector comprehensive (exit code $scanner_rc)."
    fi
  fi
  if ((COLLECTION_CANCELLED == 0 && COLLECTION_TIMED_OUT == 0)) && collector_enabled "$out" artifact-validation; then
    if collector_should_run "$out" artifact-validation; then
      collector_mark "$out" artifact-validation RUNNING
    if run_bounded artifact-validation 300 "$out/artifact-smoke.log" "$PYTHON_BIN" "$VALIDATOR" "$out"; then validator_rc=0; else validator_rc=$?; fi
      collector_finish "$out" artifact-validation "$validator_rc"
    else
      validator_rc="$(collector_existing_rc "$out" artifact-validation)"; echo "Reutilizando collector artifact-validation (exit code $validator_rc)."
    fi
  fi
  if ((COLLECTION_CANCELLED == 0 && COLLECTION_TIMED_OUT == 0)) && collector_enabled "$out" contract-validation; then
    if collector_should_run "$out" contract-validation; then
      collector_mark "$out" contract-validation RUNNING
      if run_bounded contract-validation 120 "$out/contract-validation.log" "$PYTHON_BIN" "$CONTRACT_VALIDATOR" --collection "$out"; then contract_rc=0; else contract_rc=$?; fi
      collector_finish "$out" contract-validation "$contract_rc"
    else
      contract_rc="$(collector_existing_rc "$out" contract-validation)"; echo "Reutilizando collector contract-validation (exit code $contract_rc)."
    fi
  fi

  codes="$(jq -nc --argjson p "$preflight_rc" --argjson a "$assess_rc" --argjson d "$discovery_rc" --argjson m "$configuration_rc" --argjson t "$telemetry_rc" --argjson n "$node_rc" --argjson s "$scanner_rc" --argjson v "$validator_rc" --argjson c "$contract_rc" '[$p,$a,$d,$m,$t,$n,$s,$v,$c]')"
  status=COMPLETED; reason=''; completed=true
  if ((COLLECTION_CANCELLED == 1)); then status=CANCELLED; reason='operator requested cancellation'; completed=false
  elif ((COLLECTION_TIMED_OUT == 1)); then status=TIMED_OUT; reason="collection exceeded ${MAX_DURATION_SECONDS}s"; completed=false
  else
    required_failures="$(jq -r '. as $root | [$root.plan[] | select(.required) | select(($root.collectors[.id].state // "PENDING") != "PASS")] | length' "$out/collector-state.json")"
    if ((required_failures > 0)); then status=FAILED; completed=false; reason="$required_failures required collector(s) did not pass"; fi
  fi
  write_metadata "$out" "$id" "$phase" "$cluster_name" "$cluster_context" "$baseline" "$completed" "$codes" "$status" "$reason" "$MAX_DURATION_SECONDS" "$namespace"
  if [[ "$status" == COMPLETED ]] && collector_enabled "$out" contract-validation; then
    if "$PYTHON_BIN" "$CONTRACT_VALIDATOR" --collection "$out" >> "$out/contract-validation.log" 2>&1; then
      contract_rc=0
      collector_mark "$out" contract-validation PASS 0 'final terminal metadata validated'
    else
      contract_rc=$?
      collector_mark "$out" contract-validation FAIL "$contract_rc" 'final terminal metadata failed validation'
      status=FAILED; completed=false; reason='final JSON Schema validation failed'
    fi
    codes="$(jq -nc --argjson p "$preflight_rc" --argjson a "$assess_rc" --argjson d "$discovery_rc" --argjson m "$configuration_rc" --argjson t "$telemetry_rc" --argjson n "$node_rc" --argjson s "$scanner_rc" --argjson v "$validator_rc" --argjson c "$contract_rc" '[$p,$a,$d,$m,$t,$n,$s,$v,$c]')"
    write_metadata "$out" "$id" "$phase" "$cluster_name" "$cluster_context" "$baseline" "$completed" "$codes" "$status" "$reason" "$MAX_DURATION_SECONDS" "$namespace"
  fi
  COLLECTION_STARTED_EPOCH=0
  echo "Salvo em $out | status: $status"
  echo "Contexto: $cluster_context | códigos [preflight, assessment, discovery, configuration metadata, Prometheus, node evidence, scanner, artifacts, contracts]: $codes"
  if ((NON_INTERACTIVE == 1)); then
    printf 'COLLECTION_ID=%s\nCOLLECTION_PATH=%s\nCOLLECTION_STATUS=%s\n' "$id" "$out" "$status"
    case "$status" in
      COMPLETED) return 0 ;;
      TIMED_OUT) return 124 ;;
      CANCELLED) return 130 ;;
      *) return 1 ;;
    esac
  fi
  return 0
}

provider_gate(){
  local id dir expected rc latest
  id="${1:-}"
  expected="${2:-}"
  latest="$(collections | tail -1)"
  if [[ -z "$latest" ]]; then
    echo "Nenhuma coleta disponível para validação." >&2
    ((NON_INTERACTIVE == 0)) || return 2
    return 0
  fi
  if [[ -z "$id" ]]; then
    collections | nl -ba
    read -r -p "ID da coleta (Enter = $latest): " id
    id="${id:-$latest}"
  fi
  if ! valid_collection_id "$id"; then
    echo "ID da coleta inválido."
    return 0
  fi
  dir="$OUTROOT/$id"
  if [[ ! -d "$dir" ]]; then
    echo "Coleta não encontrada." >&2
    ((NON_INTERACTIVE == 0)) || return 2
    return 0
  fi
  [[ -n "$expected" ]] || read -r -p 'Provider esperado [eks|aks|gke|generic-kubernetes]: ' expected
  case "$expected" in
    eks|aks|gke|generic-kubernetes) ;;
    *) echo "Provider esperado inválido; a expectativa deve ser explícita."; return 0 ;;
  esac
  echo "Executando Release Gate offline; nenhuma API do cluster ou do provider será consultada."
  if "$PYTHON_BIN" "$PROVIDER_VALIDATOR" --collection "$dir" --expected-provider "$expected"; then
    rc=0
  else
    rc=$?
  fi
  if ((rc == 0 || rc == 1)) && [[ -r "$dir/provider-validation.json" ]]; then
    jq -r '"Estado: \(.summary.state) | Release Ready: \(.summary.releaseReady) | Gates: \(.summary.gates) | PASS: \(.summary.status.PASS // 0) | WARN: \(.summary.status.WARN // 0) | FAIL: \(.summary.status.FAIL // 0) | N/A: \(.summary.status["N/A"] // 0)"' "$dir/provider-validation.json"
    echo "JSON: $dir/provider-validation.json"
    echo "JUnit: $dir/provider-validation.junit.xml"
    echo "SARIF: $dir/provider-validation.sarif.json"
    echo "Markdown: $dir/provider-validation.md"
  else
    echo "ERRO: o Release Gate não pôde ser executado (exit code $rc)." >&2
  fi
  ((NON_INTERACTIVE == 1)) && return "$rc"
  return 0
}

regression_gate(){
  local before_id after_id before after profile profiles default_profile rc latest previous
  local -a available=()
  before_id="${1:-}"; after_id="${2:-}"; profile="${3:-}"
  mapfile -t available < <(collections)
  if ((${#available[@]} < 2)); then
    echo "São necessárias ao menos duas coletas para o Regression Gate." >&2
    ((NON_INTERACTIVE == 0)) || return 2
    return 0
  fi
  latest="${available[${#available[@]}-1]}"
  previous="${available[${#available[@]}-2]}"
  if [[ -z "$before_id" || -z "$after_id" ]]; then
    collections | nl -ba
    read -r -p "ID da coleta ANTERIOR (Enter = $previous): " before_id
    read -r -p "ID da coleta ATUAL (Enter = $latest): " after_id
    before_id="${before_id:-$previous}"; after_id="${after_id:-$latest}"
  fi
  if ! valid_collection_id "$before_id" || ! valid_collection_id "$after_id"; then
    echo "ID de coleta inválido."
    return 0
  fi
  if [[ "$before_id" == "$after_id" ]]; then
    echo "As coletas anterior e atual devem ser diferentes."
    return 0
  fi
  before="$OUTROOT/$before_id"; after="$OUTROOT/$after_id"
  if [[ ! -d "$before" || ! -d "$after" ]]; then
    echo "Coleta não encontrada." >&2
    ((NON_INTERACTIVE == 0)) || return 2
    return 0
  fi
  if [[ ! -r "$REGRESSION_POLICY" ]]; then
    echo "ERRO: policy do Regression Gate não encontrada em $REGRESSION_POLICY" >&2
    return 0
  fi
  profiles="$(jq -r '.profiles | keys | join("|")' "$REGRESSION_POLICY" 2>/dev/null || true)"
  default_profile="$(jq -r '.defaultProfile // empty' "$REGRESSION_POLICY" 2>/dev/null || true)"
  if [[ -z "$profiles" || -z "$default_profile" ]]; then
    echo "ERRO: policy do Regression Gate inválida." >&2
    return 0
  fi
  if [[ -z "$profile" ]]; then
    if ((NON_INTERACTIVE == 1)); then profile="$default_profile"; else read -r -p "Policy profile [$profiles] (Enter = $default_profile): " profile; fi
  fi
  profile="${profile:-$default_profile}"
  if ! jq -e --arg profile "$profile" '.profiles[$profile] | type == "object"' "$REGRESSION_POLICY" >/dev/null 2>&1; then
    echo "Policy profile inválido."
    return 0
  fi
  echo "Executando Regression Gate offline; nenhuma API do cluster ou do provider será consultada."
  if "$PYTHON_BIN" "$REGRESSION_VALIDATOR" --before "$before" --after "$after" --policy "$REGRESSION_POLICY" --profile "$profile"; then
    rc=0
  else
    rc=$?
  fi
  if ((rc == 0 || rc == 1)) && [[ -r "$after/regression-validation.json" ]]; then
    jq -r '"Estado: \(.summary.state) | Release Ready: \(.summary.releaseReady) | Profile: \(.policy.profile) | Novos riscos: \(.summary.newRisks) | Regressões: \(.summary.severityRegressions) | Evidence Loss: \(.summary.evidenceLoss)"' "$after/regression-validation.json"
    echo "JSON: $after/regression-validation.json"
    echo "JUnit: $after/regression-validation.junit.xml"
    echo "SARIF: $after/regression-validation.sarif.json"
  else
    echo "ERRO: o Regression Gate não pôde ser executado (exit code $rc)." >&2
  fi
  ((NON_INTERACTIVE == 1)) && return "$rc"
  return 0
}

blue_green_gate(){
  local id dir latest rc url
  local -a args
  id="${1:-}"
  latest="$(collections | tail -1)"
  if [[ -z "$latest" ]]; then
    echo "Nenhuma coleta disponível para Blue-Green Readiness." >&2
    ((NON_INTERACTIVE == 0)) || return 2
    return 0
  fi
  if [[ -z "$id" ]]; then
    collections | nl -ba
    read -r -p "ID da coleta (Enter = $latest): " id
    id="${id:-$latest}"
  fi
  if ! valid_collection_id "$id"; then
    echo "ID da coleta inválido." >&2
    ((NON_INTERACTIVE == 0)) || return 2
    return 0
  fi
  dir="$OUTROOT/$id"
  if [[ ! -d "$dir" ]]; then
    echo "Coleta não encontrada." >&2
    ((NON_INTERACTIVE == 0)) || return 2
    return 0
  fi
  args=("$PYTHON_BIN" "$BLUE_GREEN_READINESS" --collection "$dir")
  if ((NON_INTERACTIVE == 0)); then
    read -r -p 'URL HTTP/HTTPS para probe de cutover (Enter = nenhuma): ' url
    [[ -z "$url" ]] || args+=(--probe-url "$url")
  else
    for url in "${CLI_PROBE_URLS[@]}"; do args+=(--probe-url "$url"); done
  fi
  echo "Gerando Blue-Green Readiness read-only; nenhum cutover ou alteração será executado."
  if "${args[@]}"; then rc=0; else rc=$?; fi
  if ((rc == 0 || rc == 1)) && [[ -r "$dir/blue-green-readiness.json" ]]; then
    jq -r '"Status: \(.status) | Gates: \(.summary.gates) | Bloqueios: \(.summary.blocking) | Desconhecidos: \(.summary.unknown) | Alertas: \(.summary.warnings)"' "$dir/blue-green-readiness.json"
    echo "Readiness: $dir/blue-green-readiness.json"
    echo "Configuration References: $dir/configuration-references.json"
    echo "Traffic Paths: $dir/traffic-paths.json"
    echo "State & Data: $dir/state-data-readiness.json"
    echo "Probes: $dir/migration-probes.json"
  else
    echo "ERRO: Blue-Green Readiness não pôde ser gerado (exit code $rc)." >&2
  fi
  ((NON_INTERACTIVE == 1)) && return "$rc"
  return 0
}

migration_gate(){
  local source_id target_id source target mapping rc latest previous
  local -a available=() args
  source_id="${1:-}"; target_id="${2:-}"; mapping="${3:-}"
  mapfile -t available < <(collections)
  if ((${#available[@]} < 2)); then
    echo "São necessárias ao menos duas coletas para o Migration Gate." >&2
    ((NON_INTERACTIVE == 0)) || return 2
    return 0
  fi
  latest="${available[${#available[@]}-1]}"
  previous="${available[${#available[@]}-2]}"
  if [[ -z "$source_id" || -z "$target_id" ]]; then
    collections | nl -ba
    read -r -p "ID source/blue (Enter = $previous): " source_id
    read -r -p "ID target/green (Enter = $latest): " target_id
    source_id="${source_id:-$previous}"; target_id="${target_id:-$latest}"
    read -r -p 'Mapping JSON opcional (Enter = mapeamento por identidade): ' mapping
  fi
  if ! valid_collection_id "$source_id" || ! valid_collection_id "$target_id" || [[ "$source_id" == "$target_id" ]]; then
    echo "Source e target devem ser IDs válidos e diferentes." >&2
    ((NON_INTERACTIVE == 0)) || return 2
    return 0
  fi
  source="$OUTROOT/$source_id"; target="$OUTROOT/$target_id"
  if [[ ! -d "$source" || ! -d "$target" ]]; then
    echo "Coleta source ou target não encontrada." >&2
    ((NON_INTERACTIVE == 0)) || return 2
    return 0
  fi
  if [[ -n "$mapping" && ! -r "$mapping" ]]; then
    echo "Mapping JSON não encontrado: $mapping" >&2
    ((NON_INTERACTIVE == 0)) || return 2
    return 0
  fi
  args=("$PYTHON_BIN" "$MIGRATION_COMPARE" --source "$source" --target "$target" --output "$target/migration-comparison.json")
  [[ -z "$mapping" ]] || args+=(--mapping "$mapping")
  echo "Comparando source e target offline; identidades de cluster e namespace podem diferir por mapping explícito."
  if "${args[@]}"; then rc=0; else rc=$?; fi
  if ((rc == 0 || rc == 1)) && [[ -r "$target/migration-comparison.json" ]]; then
    jq -r '"Status: \(.status) | Gates: \(.summary.gates) | Bloqueios: \(.summary.blocking) | Desconhecidos: \(.summary.unknown) | Workloads ausentes: \(.summary.missingWorkloads)"' "$target/migration-comparison.json"
    echo "JSON: $target/migration-comparison.json"
    echo "JUnit: $target/migration-comparison.junit.xml"
    echo "SARIF: $target/migration-comparison.sarif.json"
    echo "Markdown: $target/migration-comparison.md"
  else
    echo "ERRO: Migration Gate não pôde ser executado (exit code $rc)." >&2
  fi
  ((NON_INTERACTIVE == 1)) && return "$rc"
  return 0
}

compare(){
  local before="${1:-}" after="${2:-}"
  if [[ -z "$before" || -z "$after" ]]; then
    collections | nl -ba
    read -r -p 'ID ANTES: ' before; read -r -p 'ID DEPOIS: ' after
  fi
  if ! valid_collection_id "$before" || ! valid_collection_id "$after"; then
    echo 'IDs inválidos.' >&2
    ((NON_INTERACTIVE == 0)) || return 2
    return 0
  fi
  before="$OUTROOT/$before"; after="$OUTROOT/$after"
  if [[ ! -r "$before/comprehensive-assessment.json" || ! -r "$after/comprehensive-assessment.json" ]]; then
    echo 'Coletas abrangentes inválidas.' >&2
    ((NON_INTERACTIVE == 0)) || return 2
    return 0
  fi
  jq -n --slurpfile before "$before/comprehensive-assessment.json" --slurpfile after "$after/comprehensive-assessment.json" '
    ($before[0].findings | map(select(.severity=="CRIT" or .severity=="WARN") | .id)) as $old |
    ($after[0].findings | map(select(.severity=="CRIT" or .severity=="WARN") | .id)) as $new |
    {before:$before[0].summary,after:$after[0].summary,
     delta:{critical:($after[0].summary.critical-$before[0].summary.critical),warnings:($after[0].summary.warnings-$before[0].summary.warnings),passed:($after[0].summary.passed-$before[0].summary.passed)},
     newRisks:[$after[0].findings[] | select((.severity=="CRIT" or .severity=="WARN") and (.id as $id | ($old | index($id) | not)))],
     resolvedRisks:[$before[0].findings[] | select((.severity=="CRIT" or .severity=="WARN") and (.id as $id | ($new | index($id) | not)))]}'
}

terminal(){
  local id="${1:-}" dir
  if [[ -z "$id" ]]; then collections | nl -ba; read -r -p 'ID da coleta: ' id; fi
  if ! valid_collection_id "$id"; then echo 'ID inválido.' >&2; ((NON_INTERACTIVE == 0)) || return 2; return 0; fi
  dir="$OUTROOT/$id"
  if [[ ! -d "$dir" ]]; then echo 'Coleta não encontrada.' >&2; ((NON_INTERACTIVE == 0)) || return 2; return 0; fi
  ((NON_INTERACTIVE == 1)) || clear
  echo 'EKS ENVIRONMENT - DASHBOARD TERMINAL'
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

dashboard_port_in_use(){
  (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null
}
dashboard_listener_pid(){
  command -v ss >/dev/null 2>&1 || return 1
  ss -ltnp 2>/dev/null | awk -v port=":$PORT" '$4 ~ (port "$") {print}' | sed -n 's/.*pid=\([0-9][0-9]*\).*/\1/p' | head -1
}
assessment_dashboard_pid(){
  local pid command_line
  pid="$(dashboard_listener_pid)"
  [[ "$pid" =~ ^[0-9]+$ && -r "/proc/$pid/cmdline" ]] || return 1
  command_line="$(tr '\0' ' ' < "/proc/$pid/cmdline")"
  [[ "$command_line" == *assessment_dashboard.py* && "$command_line" == *"--port $PORT"* ]] || return 1
  printf '%s\n' "$pid"
}
next_dashboard_port(){
  local candidate original="$PORT"
  for ((candidate=original + 1; candidate <= original + 100; candidate++)); do
    PORT="$candidate"
    if ! dashboard_port_in_use; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  PORT="$original"
  return 1
}
stop_assessment_dashboard(){
  local pid="$1" attempt
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  assessment_dashboard_pid | grep -qx "$pid" || return 1
  kill -TERM "$pid" 2>/dev/null || return 1
  for attempt in {1..30}; do
    kill -0 "$pid" 2>/dev/null || return 0
    sleep 0.1
  done
  return 1
}
dashboard_host_rows(){
  local override="${DASHBOARD_PUBLIC_HOST:-}" ssh_server="" interface cidr host
  declare -A seen=()
  if [[ -n "$override" ]]; then
    if [[ "$override" =~ ^[A-Za-z0-9._:-]+$ ]]; then
      seen["$override"]=1
      printf 'Configurado\t%s\n' "$override"
    else
      echo "AVISO: DASHBOARD_PUBLIC_HOST inválido; use somente hostname ou endereço IP." >&2
    fi
  fi
  if [[ -n "${SSH_CONNECTION:-}" ]]; then
    read -r _ _ ssh_server _ <<< "$SSH_CONNECTION"
    if [[ -n "$ssh_server" && -z "${seen[$ssh_server]:-}" ]]; then
      seen["$ssh_server"]=1
      printf 'SSH/remoto\t%s\n' "$ssh_server"
    fi
  fi
  if [[ -z "${seen[127.0.0.1]:-}" ]]; then
    seen[127.0.0.1]=1
    printf 'Local neste host\t127.0.0.1\n'
  fi
  if command -v ip >/dev/null 2>&1; then
    while read -r interface cidr; do
      [[ -n "$interface" && -n "$cidr" ]] || continue
      [[ "$interface" =~ ^(lo|docker.*|podman.*|veth.*|br-.*|virbr.*|cni.*|flannel.*|cali.*|vxlan.*|tunl.*|cilium.*|kube-ipvs.*)$ ]] && continue
      host="${cidr%%/*}"
      [[ "$host" == 127.* || "$host" == 169.254.* || -n "${seen[$host]:-}" ]] && continue
      seen["$host"]=1
      printf 'Interface %s\t%s\n' "$interface" "$host"
    done < <(ip -o -4 addr show scope global 2>/dev/null | awk '{print $2, $4}')
  else
    for host in $(hostname -I 2>/dev/null); do
      [[ "$host" == *:* || "$host" == 127.* || "$host" == 169.254.* || -n "${seen[$host]:-}" ]] && continue
      seen["$host"]=1
      printf 'Interface detectada\t%s\n' "$host"
    done
  fi
}
dashboard_url_host(){
  local host="$1"
  if [[ "$host" == *:* && "$host" != \[*\] ]]; then printf '[%s]' "$host"; else printf '%s' "$host"; fi
}
dashboard_primary_host(){
  local _label host
  while IFS=$'\t' read -r _label host; do
    [[ -z "$host" ]] || { printf '%s\n' "$host"; return 0; }
  done < <(dashboard_host_rows)
  return 1
}
print_dashboard_urls(){
  local access_token="${1:-}" label host formatted suffix=""
  [[ -z "$access_token" ]] || suffix="/?access_token=$access_token"
  echo "URLs do dashboard:"
  while IFS=$'\t' read -r label host; do
    [[ -n "$host" ]] || continue
    formatted="$(dashboard_url_host "$host")"
    printf '  %-20s http://%s:%s%s\n' "$label:" "$formatted" "$PORT" "$suffix"
  done < <(dashboard_host_rows)
}
web(){
  local public_host access_token pid choice new_port
  [[ -n "$PYTHON_BIN" ]] || select_python || { echo "ERRO: Python 3.10+ ausente" >&2; return 1; }
  [[ -r "$TOOL_ROOT/web/public/styles.css" ]] || { echo "ERRO: CSS do dashboard ausente em $TOOL_ROOT/web/public/styles.css" >&2; return 1; }
  public_host="$(dashboard_primary_host)"
  public_host="${public_host:-127.0.0.1}"
  while dashboard_port_in_use; do
    if ((NON_INTERACTIVE == 1)); then
      echo "ERRO: a porta $PORT já está em uso; informe outra com --port." >&2
      return 1
    fi
    pid="$(assessment_dashboard_pid || true)"
    echo "A porta $PORT já está em uso."
    if [[ -n "$pid" ]]; then
      cat <<EOF
1) Usar o dashboard atual em http://$public_host:$PORT
2) Encerrar o dashboard atual (PID $pid) e iniciar outro na mesma porta
3) Iniciar outro dashboard na próxima porta livre
0) Voltar
EOF
      read -r -p 'Opção: ' choice
      case "$choice" in
        1)
          print_dashboard_urls
          echo "Use a sessão já autenticada no navegador ou a URL temporária exibida quando ele foi iniciado."
          return 0
          ;;
        2)
          if stop_assessment_dashboard "$pid"; then
            echo "Dashboard atual encerrado. Iniciando outro na porta $PORT."
          else
            echo "ERRO: não foi possível encerrar com segurança o dashboard PID $pid." >&2
            return 1
          fi
          ;;
        3)
          new_port="$(next_dashboard_port)" || { echo "ERRO: nenhuma porta livre encontrada entre $((PORT + 1)) e $((PORT + 100))." >&2; return 1; }
          PORT="$new_port"
          echo "Novo dashboard será iniciado na porta $PORT."
          ;;
        0) return 0 ;;
        *) echo "Opção inválida."; continue ;;
      esac
    else
      cat <<EOF
O processo da porta $PORT não foi identificado como dashboard do assessment e não será encerrado.
1) Iniciar o dashboard na próxima porta livre
0) Voltar
EOF
      read -r -p 'Opção: ' choice
      case "$choice" in
        1)
          new_port="$(next_dashboard_port)" || { echo "ERRO: nenhuma porta livre encontrada entre $((PORT + 1)) e $((PORT + 100))." >&2; return 1; }
          PORT="$new_port"
          echo "Novo dashboard será iniciado na porta $PORT."
          ;;
        0) return 0 ;;
        *) echo "Opção inválida."; continue ;;
      esac
    fi
  done
  access_token="$($PYTHON_BIN -c 'import secrets; print(secrets.token_urlsafe(32))')"
  print_dashboard_urls "$access_token"
  echo "O access token é temporário e válido somente durante esta execução."
  echo "O servidor ficará preso a esta sessão. Pressione Ctrl+C para encerrar."
  DASHBOARD_FOREGROUND=1
  "$PYTHON_BIN" "$TOOL_ROOT/src/assessment_dashboard.py" --root "$OUTROOT" --static "$TOOL_ROOT/web/public" --host 0.0.0.0 --port "$PORT" --allow-remote --access-token "$access_token"
  DASHBOARD_FOREGROUND=0
  echo "Dashboard encerrado."
}

prepare_runtime(){
  case "$COMMAND" in
    list) ;;
    compare|terminal) need jq ;;
    dashboard)
      select_python || { echo "ERRO: Python 3.10+ ausente; defina PYTHON_BIN se necessário" >&2; exit 1; }
      ;;
    release-gate)
      need jq
      select_python || { echo "ERRO: Python 3.10+ ausente; defina PYTHON_BIN se necessário" >&2; exit 1; }
      [[ -r "$PROVIDER_VALIDATOR" ]] || { echo "ERRO: Provider Validation Runner ausente em $PROVIDER_VALIDATOR" >&2; exit 1; }
      ;;
    regression-gate)
      need jq
      select_python || { echo "ERRO: Python 3.10+ ausente; defina PYTHON_BIN se necessário" >&2; exit 1; }
      [[ -r "$REGRESSION_VALIDATOR" ]] || { echo "ERRO: Regression Gate ausente em $REGRESSION_VALIDATOR" >&2; exit 1; }
      [[ -r "$REGRESSION_POLICY" ]] || { echo "ERRO: policy do Regression Gate ausente em $REGRESSION_POLICY" >&2; exit 1; }
      ;;
    blue-green-gate)
      need jq
      select_python || { echo "ERRO: Python 3.10+ ausente; defina PYTHON_BIN se necessário" >&2; exit 1; }
      [[ -r "$BLUE_GREEN_READINESS" ]] || { echo "ERRO: Blue-Green Readiness ausente em $BLUE_GREEN_READINESS" >&2; exit 1; }
      ;;
    migration-gate)
      need jq
      select_python || { echo "ERRO: Python 3.10+ ausente; defina PYTHON_BIN se necessário" >&2; exit 1; }
      [[ -r "$BLUE_GREEN_READINESS" && -r "$MIGRATION_COMPARE" ]] || { echo "ERRO: módulos do Migration Gate ausentes." >&2; exit 1; }
      ;;
    validate|bundle|prune|verify-release)
      select_python || { echo "ERRO: Python 3.10+ ausente; defina PYTHON_BIN se necessário" >&2; exit 1; }
      [[ -r "$CONTRACT_VALIDATOR" && -r "$BUNDLE_MANAGER" && -r "$RELEASE_VERIFIER" ]] || { echo "ERRO: módulos de portabilidade ausentes." >&2; exit 1; }
      ;;
    preflight)
      select_python || { echo "ERRO: Python 3.10+ ausente; defina PYTHON_BIN se necessário" >&2; exit 1; }
      [[ -r "$PREFLIGHT" ]] || { echo "ERRO: preflight ausente em $PREFLIGHT" >&2; exit 1; }
      ;;
    menu|collect)
      need kubectl; need jq; need curl; need timeout; need setsid
      select_python || { echo "ERRO: Python 3.10+ ausente; defina PYTHON_BIN se necessário" >&2; exit 1; }
      [[ -r "$PREFLIGHT" ]] || { echo "ERRO: preflight ausente em $PREFLIGHT" >&2; exit 1; }
      [[ -r "$PROVIDER_VALIDATOR" ]] || { echo "ERRO: Provider Validation Runner ausente em $PROVIDER_VALIDATOR" >&2; exit 1; }
      [[ -r "$REGRESSION_VALIDATOR" ]] || { echo "ERRO: Regression Gate ausente em $REGRESSION_VALIDATOR" >&2; exit 1; }
      [[ -r "$REGRESSION_POLICY" ]] || { echo "ERRO: policy do Regression Gate ausente em $REGRESSION_POLICY" >&2; exit 1; }
      [[ -r "$CONFIGURATION_METADATA" && -r "$BLUE_GREEN_READINESS" && -r "$MIGRATION_COMPARE" ]] || { echo "ERRO: módulos de Blue-Green Readiness ausentes." >&2; exit 1; }
      [[ -r "$COLLECTOR_MANAGER" && -r "$COLLECTOR_REGISTRY" && -r "$CONTRACT_VALIDATOR" && -r "$NODE_EVIDENCE" ]] || { echo "ERRO: módulos do collector registry ausentes." >&2; exit 1; }
      ;;
  esac
  mkdir -p "$OUTROOT"
}

prepare_runtime

if ((NON_INTERACTIVE == 1)); then
  case "$COMMAND" in
    preflight) run_preflight "$CLI_PROMETHEUS_URL" "${EKS_CLUSTER_NAME:-}" "$CLI_NAMESPACE" ;;
    collect) collect "$CLI_PHASE" ;;
    list) collections ;;
    compare) compare "$CLI_BEFORE" "$CLI_AFTER" ;;
    terminal) terminal "$CLI_COLLECTION" ;;
    dashboard) web ;;
    release-gate) provider_gate "$CLI_COLLECTION" "$CLI_PROVIDER" ;;
    regression-gate) regression_gate "$CLI_BEFORE" "$CLI_AFTER" "$CLI_PROFILE" ;;
    blue-green-gate) blue_green_gate "$CLI_COLLECTION" ;;
    migration-gate) migration_gate "$CLI_SOURCE" "$CLI_TARGET" "$CLI_MAPPING" ;;
    validate) validate_collection_contracts "$CLI_COLLECTION" ;;
    bundle) bundle_command ;;
    prune) prune_collections ;;
    verify-release) verify_release ;;
    menu) ;;
  esac
  exit $?
fi

while :; do
  render_menu
  read -r -p "${C_BOLD}${C_LIGHT}Selecione uma opção › ${C_RESET}" op
  case "$op" in
    1) collect before;; 2) collect after;; 3) compare;; 4) terminal;;
    5) web;; 6) run_preflight "${PROMETHEUS_URL:-}" "${EKS_CLUSTER_NAME:-}";; 7) provider_gate;; 8) regression_gate;;
    9) interactive_validate;; 10) interactive_bundle;;
    11) blue_green_gate;; 12) migration_gate;;
    0) printf '%sSessão encerrada.%s\n' "$C_DIM" "$C_RESET"; exit 0;; *) printf '%sOpção inválida.%s\n' "$C_RED" "$C_RESET";;
  esac
  [[ "$op" == 0 || "$op" == 5 ]] || read -r -p 'Enter para continuar…' _
done
