#!/usr/bin/env bash
set -euo pipefail

# Read-only EKS assessment. Produces a point-in-time health and best-practices
# report, including Prometheus baseline data, without changing cluster state.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
AWS_SCANNER="$ROOT/scripts/validation/aws_eks_assessment.py"
EKS_CLUSTER_NAME="${EKS_CLUSTER_NAME:-}"
AWS_REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-}}"
PROMETHEUS_NAMESPACE="${PROMETHEUS_NAMESPACE:-monitoring}"
PROMETHEUS_SERVICE="${PROMETHEUS_SERVICE:-kube-prometheus-stack-prometheus}"
OUTPUT_DIR="${OUTPUT_DIR:-assessment/eks-$(date -u +%Y%m%dT%H%M%SZ)}"

require_cmd() { command -v "$1" >/dev/null 2>&1 || { echo "ERRO: comando obrigatório ausente: $1" >&2; exit 1; }; }
info() { printf '\n[INFO] %s\n' "$*"; }
warn() { printf '[WARN] %s\n' "$*" >&2; }

require_cmd kubectl
require_cmd jq
mkdir -p "$OUTPUT_DIR/prometheus"

REPORT="$OUTPUT_DIR/report.md"
FINDINGS="$OUTPUT_DIR/findings.tsv"
METRICS="$OUTPUT_DIR/prometheus-baseline.tsv"
printf 'status\tarea\titem\tevidence\n' >"$FINDINGS"
printf 'metric\tvalue\tpromql\n' >"$METRICS"

finding() {
  local status="$1" area="$2" item="$3" evidence="$4"
  printf '%s\t%s\t%s\t%s\n' "$status" "$area" "$item" "$evidence" >>"$FINDINGS"
}

section() { printf '\n## %s\n' "$*" >>"$REPORT"; }
bullet() { printf -- '- %s\n' "$*" >>"$REPORT"; }

context="$(kubectl config current-context)"
kubectl version -o json >"$OUTPUT_DIR/kubernetes-version.json"
kubectl get nodes -o json >"$OUTPUT_DIR/nodes.json"
kubectl get pods -A -o json >"$OUTPUT_DIR/pods.json"
kubectl get deployments,statefulsets,daemonsets -A -o json >"$OUTPUT_DIR/workloads.json"
kubectl get events -A --sort-by=.lastTimestamp -o json >"$OUTPUT_DIR/events.json"

cat >"$REPORT" <<EOF
# EKS assessment

- Generated (UTC): $(date -u +%FT%TZ)
- Kubernetes context: \`$context\`
- EKS cluster: \`${EKS_CLUSTER_NAME:-not-detected}\`
- Scope: read-only point-in-time assessment

## Health summary
EOF

info "Validando saúde do Kubernetes"
unready_nodes="$(jq '[.items[] | select(any(.status.conditions[]?; .type == "Ready" and .status != "True"))] | length' "$OUTPUT_DIR/nodes.json")"
pending_pods="$(jq '[.items[] | select(.status.phase == "Pending")] | length' "$OUTPUT_DIR/pods.json")"
failed_pods="$(jq '[.items[] | select(.status.phase == "Failed")] | length' "$OUTPUT_DIR/pods.json")"
unavailable_deployments="$(jq '[.items[] | select(.kind == "Deployment" and ((.status.availableReplicas // 0) < (.status.replicas // 0)))] | length' "$OUTPUT_DIR/workloads.json")"

if [[ "$unready_nodes" == 0 ]]; then finding PASS health "Nodes Ready" "all nodes Ready"; else finding FAIL health "Nodes Ready" "$unready_nodes node(s) not Ready"; fi
if [[ "$pending_pods" == 0 ]]; then finding PASS health "Pending pods" "none"; else finding WARN health "Pending pods" "$pending_pods pending pod(s)"; fi
if [[ "$failed_pods" == 0 ]]; then finding PASS health "Failed pods" "none"; else finding WARN health "Failed pods" "$failed_pods failed pod(s)"; fi
if [[ "$unavailable_deployments" == 0 ]]; then finding PASS health "Deployments" "all reported replicas available"; else finding WARN health "Deployments" "$unavailable_deployments deployment(s) below desired availability"; fi

bullet "Nodes not Ready: $unready_nodes"
bullet "Pending pods: $pending_pods"
bullet "Failed pods: $failed_pods"
bullet "Deployments below desired availability: $unavailable_deployments"

section "Kubernetes best practices"
info "Avaliando práticas Kubernetes"
kubectl get networkpolicy -A -o json >"$OUTPUT_DIR/networkpolicies.json" 2>/dev/null || printf '{"items":[]}' >"$OUTPUT_DIR/networkpolicies.json"
kubectl get poddisruptionbudget -A -o json >"$OUTPUT_DIR/poddisruptionbudgets.json" 2>/dev/null || printf '{"items":[]}' >"$OUTPUT_DIR/poddisruptionbudgets.json"
kubectl get namespace -o json >"$OUTPUT_DIR/namespaces.json"

network_policies="$(jq '.items | length' "$OUTPUT_DIR/networkpolicies.json")"
pdbs="$(jq '.items | length' "$OUTPUT_DIR/poddisruptionbudgets.json")"
pss_namespaces="$(jq '[.items[] | select(.metadata.labels["pod-security.kubernetes.io/enforce"] != null)] | length' "$OUTPUT_DIR/namespaces.json")"
containers_without_resources="$(jq '[.items[] | .spec.containers[]? | select(.resources.requests == null or .resources.limits == null)] | length' "$OUTPUT_DIR/pods.json")"
latest_images="$(jq '[.items[] | .spec.containers[]? | select(.image | test("(:latest$|^[^:]+$)"))] | length' "$OUTPUT_DIR/pods.json")"
privileged_containers="$(jq '[.items[] | .spec.containers[]? | select(.securityContext.privileged == true)] | length' "$OUTPUT_DIR/pods.json")"

if (( network_policies > 0 )); then finding PASS security "NetworkPolicy" "$network_policies policy object(s) found"; else finding WARN security "NetworkPolicy" "no policies found"; fi
if (( pss_namespaces > 0 )); then finding PASS security "Pod Security Standards" "$pss_namespaces namespace(s) enforce PSS"; else finding WARN security "Pod Security Standards" "no namespace enforce label found"; fi
if (( pdbs > 0 )); then finding PASS reliability "PodDisruptionBudget" "$pdbs PDB(s) found"; else finding WARN reliability "PodDisruptionBudget" "no PDB found"; fi
if (( containers_without_resources == 0 )); then finding PASS reliability "Resources" "all running containers define requests and limits"; else finding WARN reliability "Resources" "$containers_without_resources container(s) missing requests or limits"; fi
if (( latest_images == 0 )); then finding PASS supply-chain "Image tags" "no latest/untagged running image"; else finding WARN supply-chain "Image tags" "$latest_images latest/untagged running image(s)"; fi
if (( privileged_containers == 0 )); then finding PASS security "Privileged containers" "none found"; else finding FAIL security "Privileged containers" "$privileged_containers privileged container(s)"; fi

bullet "NetworkPolicies: $network_policies"
bullet "PSS-enforcing namespaces: $pss_namespaces"
bullet "PDBs: $pdbs"
bullet "Containers missing requests/limits: $containers_without_resources"
bullet "Latest or untagged images: $latest_images"
bullet "Privileged containers: $privileged_containers"

prom_query() {
  local name="$1" query="$2" encoded response value
  encoded="$(printf '%s' "$query" | jq -sRr @uri)"
  if ! response="$(kubectl get --raw "/api/v1/namespaces/${PROMETHEUS_NAMESPACE}/services/http:${PROMETHEUS_SERVICE}:9090/proxy/api/v1/query?query=${encoded}" 2>&1)"; then
    finding WARN observability "Prometheus ${name}" "query unavailable: ${response//$'\n'/ }"
    return 0
  fi
  if ! jq -e '.status == "success"' >/dev/null <<<"$response"; then
    finding WARN observability "Prometheus ${name}" "unsuccessful response"
    return 0
  fi
  printf '%s\n' "$response" >"$OUTPUT_DIR/prometheus/${name}.json"
  value="$(jq -r '[.data.result[]?.value[1]] | join(",")' <<<"$response")"
  printf '%s\t%s\t%s\n' "$name" "${value:-no-series}" "$query" >>"$METRICS"
}

section "Prometheus baseline"
info "Coletando linha de base do Prometheus"
prom_query nodes_ready 'sum(kube_node_status_condition{condition="Ready",status="true"})'
prom_query pods_pending 'sum(kube_pod_status_phase{phase="Pending"})'
prom_query pod_restarts_24h 'sum(increase(kube_pod_container_status_restarts_total[24h]))'
prom_query oom_killed_24h 'sum(max_over_time(kube_pod_container_status_last_terminated_reason{reason="OOMKilled"}[24h]))'
prom_query node_cpu_utilization '100 * sum(rate(node_cpu_seconds_total{mode!="idle"}[5m])) / sum(count without (cpu,mode) (node_cpu_seconds_total{mode="idle"}))'
prom_query node_memory_utilization '100 * (1 - sum(node_memory_MemAvailable_bytes) / sum(node_memory_MemTotal_bytes))'
prom_query apiserver_error_rate 'sum(rate(apiserver_request_total{code=~"5.."}[5m]))'
prom_query pvc_low_space 'count((kubelet_volume_stats_available_bytes / kubelet_volume_stats_capacity_bytes) < 0.15)'
bullet "Raw Prometheus responses: \`prometheus/*.json\`"
bullet "Metric table: \`prometheus-baseline.tsv\`"

if [[ -z "$EKS_CLUSTER_NAME" && "$context" =~ arn:aws:eks:[^:]+:[^:]+:cluster/(.+)$ ]]; then
  EKS_CLUSTER_NAME="${BASH_REMATCH[1]}"
fi

section "AWS EKS best practices"
info "Executando coletor AWS/EKS independente e somente leitura"
aws_args=(--output "$OUTPUT_DIR/aws-eks-assessment.json")
[[ -n "$EKS_CLUSTER_NAME" ]] && aws_args+=(--cluster "$EKS_CLUSTER_NAME")
[[ -n "$AWS_REGION" ]] && aws_args+=(--region "$AWS_REGION")
[[ "${ASSESSMENT_INCLUDE_ACCOUNT_SECURITY:-0}" == 1 ]] && aws_args+=(--include-account-security)
if command -v python3.11 >/dev/null 2>&1 && [[ -r "$AWS_SCANNER" ]]; then
  if python3.11 "$AWS_SCANNER" "${aws_args[@]}" >"$OUTPUT_DIR/aws-eks-assessment.log" 2>&1; then
    jq -r '.findings[]? | [.severity, (.category | ascii_downcase), .check, .detail] | @tsv' "$OUTPUT_DIR/aws-eks-assessment.json" >>"$FINDINGS"
    aws_state="$(jq -r '.state // "UNKNOWN"' "$OUTPUT_DIR/aws-eks-assessment.json")"
    bullet "AWS/EKS collector state: $aws_state; evidence: aws-eks-assessment.json."
  else
    finding UNKNOWN eks "AWS/EKS collector" "collector failed; inspect aws-eks-assessment.log"
    bullet "AWS/EKS collector failed; Kubernetes assessment remains valid."
  fi
else
  finding UNKNOWN eks "AWS/EKS collector" "Python 3.11 or aws_eks_assessment.py unavailable"
  bullet "AWS/EKS evidence unavailable; Kubernetes assessment remains valid."
fi
section "Findings"
printf '\n| Status | Area | Item | Evidence |\n|---|---|---|---|\n' >>"$REPORT"
awk -F'\t' 'NR > 1 {gsub(/\|/, "\\|", $4); printf "| %s | %s | %s | %s |\n", $1, $2, $3, $4}' "$FINDINGS" >>"$REPORT"

printf '\nAssessment saved to %s\n' "$OUTPUT_DIR"
awk -F'\t' 'NR > 1 {count[$1]++} END {for (status in count) printf "%s: %s\n", status, count[status]}' "$FINDINGS" | sort
