#!/usr/bin/env bash
# Read-only portability and access preflight for the Kubernetes/EKS assessment.
set -uo pipefail

REQUEST_TIMEOUT="${ASSESSMENT_PREFLIGHT_TIMEOUT:-10s}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROMETHEUS_URL="${PROMETHEUS_URL:-}"
EKS_CLUSTER_NAME="${EKS_CLUSTER_NAME:-}"
ASSESSMENT_NAMESPACE="${ASSESSMENT_NAMESPACE:-}"
AKS_CLUSTER_NAME="${AKS_CLUSTER_NAME:-}"
AKS_RESOURCE_GROUP="${AKS_RESOURCE_GROUP:-${AZURE_RESOURCE_GROUP:-}}"
GKE_CLUSTER_NAME="${GKE_CLUSTER_NAME:-}"
GKE_LOCATION="${GKE_LOCATION:-}"
GCP_PROJECT="${GCP_PROJECT:-${GOOGLE_CLOUD_PROJECT:-}}"
PYTHON_BIN="${PYTHON_BIN:-}"
passed=0
warnings=0
failed=0
context=""
cluster_ref=""

ok() { printf '[PASS] %-22s %s\n' "$1" "$2"; ((passed+=1)); }
warning() { printf '[WARN] %-22s %s\n' "$1" "$2"; ((warnings+=1)); }
failure() { printf '[FAIL] %-22s %s\n' "$1" "$2"; ((failed+=1)); }
not_applicable() { printf '[N/A ] %-22s %s\n' "$1" "$2"; }

select_python() {
  local candidate
  if [[ -n "$PYTHON_BIN" ]]; then
    command -v "$PYTHON_BIN" >/dev/null 2>&1 &&
      "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)' >/dev/null 2>&1
    return
  fi
  for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" >/dev/null 2>&1 &&
       "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)' >/dev/null 2>&1; then
      PYTHON_BIN="$candidate"
      return 0
    fi
  done
  return 1
}

if [[ ! "$REQUEST_TIMEOUT" =~ ^[1-9][0-9]*(ms|s|m)$ ]]; then
  warning "Timeout" "ASSESSMENT_PREFLIGHT_TIMEOUT inválido; usando 10s"
  REQUEST_TIMEOUT=10s
fi

for command_name in kubectl jq curl timeout setsid; do
  if command -v "$command_name" >/dev/null 2>&1; then
    ok "Dependência" "$command_name disponível"
  else
    failure "Dependência" "$command_name ausente"
  fi
done
if select_python; then
  ok "Python" "$("$PYTHON_BIN" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))') em $PYTHON_BIN"
else
  failure "Python" "Python 3.10+ não encontrado; defina PYTHON_BIN se necessário"
fi

if ((failed > 0)); then
  printf '\nPreflight: PASS=%s WARN=%s FAIL=%s\n' "$passed" "$warnings" "$failed"
  exit 1
fi

if context="$(kubectl config current-context 2>/dev/null)" && [[ -n "$context" ]]; then
  ok "Kubeconfig" "contexto atual disponível"
else
  failure "Kubeconfig" "contexto Kubernetes ausente ou inválido"
fi
cluster_ref="$(kubectl config view --minify -o jsonpath='{.contexts[0].context.cluster}' 2>/dev/null || true)"
provider_ids="$(kubectl --request-timeout="$REQUEST_TIMEOUT" get nodes -o jsonpath='{range .items[*]}{.spec.providerID}{"\n"}{end}' 2>/dev/null || true)"

api_ready=0
if kubectl --request-timeout="$REQUEST_TIMEOUT" version -o json >/dev/null 2>&1; then
  ok "API Kubernetes" "conectividade confirmada"
  api_ready=1
else
  failure "API Kubernetes" "servidor inacessível ou credenciais inválidas"
fi

check_required_access() {
  local verb="$1" resource="$2"
  shift 2
  if kubectl --request-timeout="$REQUEST_TIMEOUT" auth can-i "$verb" "$resource" "$@" >/dev/null 2>&1; then
    ok "RBAC obrigatório" "$verb $resource $*"
  else
    failure "RBAC obrigatório" "$verb $resource $*"
  fi
}

check_optional_access() {
  local verb="$1" resource="$2"
  shift 2
  if kubectl --request-timeout="$REQUEST_TIMEOUT" auth can-i "$verb" "$resource" "$@" >/dev/null 2>&1; then
    ok "RBAC ampliado" "$verb $resource $*"
  else
    warning "RBAC ampliado" "$verb $resource indisponível; cobertura ficará PARTIAL"
  fi
}

if ((api_ready == 1)); then
  check_optional_access get nodes
  check_optional_access list nodes.metrics.k8s.io
  if [[ -n "$ASSESSMENT_NAMESPACE" ]]; then
    check_required_access list pods -n "$ASSESSMENT_NAMESPACE"
    check_optional_access list pods.metrics.k8s.io -n "$ASSESSMENT_NAMESPACE"
    check_required_access list deployments.apps -n "$ASSESSMENT_NAMESPACE"
    check_optional_access get "namespace/$ASSESSMENT_NAMESPACE"
  else
    check_required_access list namespaces
    check_required_access list pods --all-namespaces
    check_optional_access list pods.metrics.k8s.io --all-namespaces
    check_required_access list deployments.apps --all-namespaces
  fi
  check_optional_access list customresourcedefinitions.apiextensions.k8s.io
  check_optional_access list clusterroles.rbac.authorization.k8s.io
  check_optional_access list storageclasses.storage.k8s.io
  if kubectl --request-timeout="$REQUEST_TIMEOUT" api-resources --verbs=list -o name >/dev/null 2>&1; then
    ok "API discovery" "recursos listáveis podem ser descobertos"
  else
    warning "API discovery" "inventário universal ficará PARTIAL"
  fi
fi

detected_eks_cluster="$EKS_CLUSTER_NAME"
detected_eks_region="${AWS_REGION:-${AWS_DEFAULT_REGION:-}}"
for eks_reference in "$cluster_ref" "$context"; do
  if [[ -z "$detected_eks_cluster" && "$eks_reference" =~ arn:[^:]+:eks:([^:]+):[0-9]+:cluster/(.+)$ ]]; then
    detected_eks_region="${detected_eks_region:-${BASH_REMATCH[1]}}"
    detected_eks_cluster="${BASH_REMATCH[2]}"
  fi
done
if [[ -n "$detected_eks_cluster" || "$provider_ids" == *"aws:///"* ]]; then
  ok "Plataforma" "Amazon EKS identificado; enriquecimento AWS é opcional"
  if [[ -z "$detected_eks_cluster" ]]; then
    warning "AWS/EKS escopo" "defina EKS_CLUSTER_NAME para consultar a AWS API"
  fi
  if command -v aws >/dev/null 2>&1; then
    if AWS_PAGER="" timeout --signal=TERM 15s aws sts get-caller-identity --output json >/dev/null 2>&1; then
      ok "AWS identidade" "credenciais válidas; permissões de serviço são verificadas separadamente"
      if [[ -n "$detected_eks_cluster" ]]; then
        eks_preflight_args=(eks describe-cluster --name "$detected_eks_cluster" --output json --no-cli-pager)
        [[ -n "$detected_eks_region" ]] && eks_preflight_args+=(--region "$detected_eks_region")
        if AWS_PAGER="" timeout --signal=TERM 20s aws "${eks_preflight_args[@]}" >/dev/null 2>&1; then
          ok "AWS EKS" "eks:DescribeCluster disponível"
        else
          warning "AWS EKS" "eks:DescribeCluster indisponível; cobertura AWS/EKS ficará PARTIAL"
        fi
      fi
    else
      warning "AWS" "CLI disponível, mas identidade/endpoint AWS está indisponível"
    fi
  else
    warning "AWS" "CLI ausente; scan Kubernetes continuará e AWS/EKS ficará UNKNOWN"
  fi
else
  not_applicable "AWS/EKS" "contexto não identificado como EKS; scan Kubernetes permanece completo"
fi

if [[ "$provider_ids" == *"azure:///"* || -n "$AKS_CLUSTER_NAME" ]]; then
  ok "Plataforma" "Azure Kubernetes Service identificado; enriquecimento Azure é opcional"
  if ! command -v az >/dev/null 2>&1; then
    warning "Azure CLI" "az ausente; scan Kubernetes continuará e AKS ficará UNKNOWN"
  elif [[ -z "$AKS_CLUSTER_NAME" || -z "$AKS_RESOURCE_GROUP" ]]; then
    warning "AKS escopo" "defina AKS_CLUSTER_NAME e AKS_RESOURCE_GROUP"
  elif timeout --signal=TERM 25s az aks show --resource-group "$AKS_RESOURCE_GROUP" --name "$AKS_CLUSTER_NAME" --only-show-errors --output none >/dev/null 2>&1; then
    ok "Azure AKS" "Microsoft.ContainerService/managedClusters/read disponível"
  else
    warning "Azure AKS" "aks show indisponível; cobertura AKS ficará PARTIAL"
  fi
else
  not_applicable "Azure/AKS" "contexto não identificado como AKS"
fi

if [[ "$context" =~ ^gke_([^_]+)_([^_]+)_(.+)$ ]]; then
  GCP_PROJECT="${GCP_PROJECT:-${BASH_REMATCH[1]}}"
  GKE_LOCATION="${GKE_LOCATION:-${BASH_REMATCH[2]}}"
  GKE_CLUSTER_NAME="${GKE_CLUSTER_NAME:-${BASH_REMATCH[3]}}"
fi
if [[ "$provider_ids" == *"gce://"* || -n "$GKE_CLUSTER_NAME" ]]; then
  ok "Plataforma" "Google Kubernetes Engine identificado; enriquecimento Google Cloud é opcional"
  if ! command -v gcloud >/dev/null 2>&1; then
    warning "Google Cloud CLI" "gcloud ausente; scan Kubernetes continuará e GKE ficará UNKNOWN"
  elif [[ -z "$GKE_CLUSTER_NAME" || -z "$GKE_LOCATION" || -z "$GCP_PROJECT" ]]; then
    warning "GKE escopo" "defina GKE_CLUSTER_NAME, GKE_LOCATION e GCP_PROJECT"
  elif timeout --signal=TERM 25s gcloud container clusters describe "$GKE_CLUSTER_NAME" --location "$GKE_LOCATION" --project "$GCP_PROJECT" --format=none --quiet >/dev/null 2>&1; then
    ok "Google GKE" "container.clusters.get disponível"
  else
    warning "Google GKE" "clusters describe indisponível; cobertura GKE ficará PARTIAL"
  fi
else
  not_applicable "Google/GKE" "contexto não identificado como GKE"
fi

if [[ -n "$PROMETHEUS_URL" ]]; then
  authority="${PROMETHEUS_URL#*://}"
  authority="${authority%%/*}"
  if [[ "$PROMETHEUS_URL" != http://* && "$PROMETHEUS_URL" != https://* ]]; then
    failure "Prometheus" "URL deve usar HTTP ou HTTPS"
  elif [[ "$authority" == *@* || "$PROMETHEUS_URL" == *"?"* || "$PROMETHEUS_URL" == *"#"* ]]; then
    failure "Prometheus" "credenciais, query string e fragmento não são aceitos na URL"
  elif ! "$PYTHON_BIN" "$SCRIPT_DIR/prometheus_telemetry.py" --url "$PROMETHEUS_URL" --validate-only >/dev/null 2>&1; then
    failure "Prometheus" "destino não permitido, não resolvível ou fora da allowlist"
  elif curl --fail --silent --show-error --max-time 10 --request GET -- "${PROMETHEUS_URL%/}/api/v1/status/runtimeinfo" >/dev/null 2>&1; then
    ok "Prometheus" "endpoint explícito acessível por HTTP GET"
  else
    warning "Prometheus" "endpoint explícito indisponível; telemetria ficará UNAVAILABLE"
  fi
else
  not_applicable "Prometheus" "URL não configurada; ausência de métrica não será conformidade"
fi

printf '\nPreflight: PASS=%s WARN=%s FAIL=%s\n' "$passed" "$warnings" "$failed"
((failed == 0))
