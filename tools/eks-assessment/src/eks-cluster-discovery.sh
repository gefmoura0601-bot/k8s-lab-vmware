#!/usr/bin/env bash
#
# Read-only Kubernetes/EKS cluster discovery report.
# Inspired by the safety model of aws-samples/sample-eks-cluster-discovery-tool,
# but implemented for this repository and its GitOps topology.
# It never applies, patches, deletes, restarts or requests Secret/ConfigMap values.
set -euo pipefail

NAMESPACE=""
SINCE="24h"
OUTPUT_DIR=""
TIMEOUT="20s"
DELAY_MS=150
MAX_LINES=250
LARGE_CLUSTER=false
COMBINED=false

usage() {
  cat <<'EOF'
Usage: eks-cluster-discovery.sh [options]

Read-only inventory and health report. Secret values are never collected.
  -n, --namespace NAME     Limit namespaced sections to NAME
  -s, --since DURATION     Event lookback accepted by kubectl (default: 24h)
  -o, --output-dir DIR     Write report files to DIR (otherwise stdout only)
      --timeout DURATION   kubectl request timeout (default: 20s)
      --delay-ms NUMBER    Delay between API calls (default: 150)
  -L, --large-cluster      Use conservative output/API limits
      --combined-report    Also create one combined text report (with -o)
  -h, --help               Show this help

Examples:
  ./tools/eks-assessment/src/eks-cluster-discovery.sh -n apps -o ./assessment
  ./tools/eks-assessment/src/eks-cluster-discovery.sh -L --delay-ms 500 -o ./assessment
EOF
}

while (($#)); do
  case "$1" in
    -n|--namespace) NAMESPACE="${2:?namespace required}"; shift 2 ;;
    -s|--since) SINCE="${2:?duration required}"; shift 2 ;;
    -o|--output-dir) OUTPUT_DIR="${2:?output directory required}"; shift 2 ;;
    --timeout) TIMEOUT="${2:?timeout required}"; shift 2 ;;
    --delay-ms) DELAY_MS="${2:?delay required}"; shift 2 ;;
    -L|--large-cluster) LARGE_CLUSTER=true; shift ;;
    --combined-report) COMBINED=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

command -v kubectl >/dev/null || { echo 'kubectl is required' >&2; exit 127; }
command -v jq >/dev/null || { echo 'jq is required' >&2; exit 127; }
[[ "$DELAY_MS" =~ ^[0-9]+$ ]] || { echo '--delay-ms must be an integer' >&2; exit 2; }
if "$LARGE_CLUSTER"; then MAX_LINES=100; DELAY_MS="${DELAY_MS:-500}"; fi
[[ "$SINCE" =~ ^([1-9][0-9]*)([smhd])$ ]] || { echo '--since must use NUMBER followed by s, m, h or d (for example 24h or 7d)' >&2; exit 2; }
case "${BASH_REMATCH[2]}" in
  s) since_unit=seconds ;;
  m) since_unit=minutes ;;
  h) since_unit=hours ;;
  d) since_unit=days ;;
esac
EVENT_CUTOFF="$(date -u -d "-${BASH_REMATCH[1]} $since_unit" +%Y-%m-%dT%H:%M:%SZ)"

if [[ -n "$OUTPUT_DIR" ]]; then
  mkdir -p "$OUTPUT_DIR"
  OUTPUT_DIR="$(cd "$OUTPUT_DIR" && pwd)"
fi
if "$COMBINED" && [[ -z "$OUTPUT_DIR" ]]; then
  echo '--combined-report requires --output-dir' >&2; exit 2
fi

scope=()
[[ -n "$NAMESPACE" ]] && scope=(-n "$NAMESPACE") || scope=(-A)
sections=0; succeeded=0; failed=0; not_applicable=0; skipped=0
combined_file="${OUTPUT_DIR:+$OUTPUT_DIR/discovery-report.txt}"
sleep_between_reads() { (( DELAY_MS > 0 )) && sleep "$((DELAY_MS / 1000)).$((DELAY_MS % 1000))"; }

emit() {
  if [[ -n "$OUTPUT_DIR" ]]; then
    tee -a "$combined_file"
  else
    cat
  fi
}

safe_name() { tr ' /:' '___' <<<"$1"; }
run_section() {
  local id="$1" title="$2"; shift 2
  local file="" result status=0
  sections=$((sections + 1))
  printf '\n[%02d/49] %s\n' "$id" "$title" | emit
  set +e
  result="$(kubectl --request-timeout="$TIMEOUT" "$@" 2>&1 | head -n "$MAX_LINES")"; status=${PIPESTATUS[0]}
  set -e
  (( status == 141 )) && status=0
  if ((status == 0)); then
    printf '%s\n' "$result" | emit
    succeeded=$((succeeded + 1))
  elif grep -qiE 'the server could not find the requested resource|doesn.t have a resource type|not found|no matches for kind' <<<"$result"; then
    printf '[N/A] Optional API or resource is not installed.\n' | emit
    not_applicable=$((not_applicable + 1))
  else
    printf '[UNAVAILABLE] kubectl exit %s: %s\n' "$status" "$result" | emit
    failed=$((failed + 1))
  fi
  if [[ -n "$OUTPUT_DIR" ]]; then
    file="$OUTPUT_DIR/$(printf '%02d' "$id")-$(safe_name "$title").txt"
    printf '%s\n' "$result" > "$file"
  fi
  # This sleeps locally; it throttles API reads without changing the cluster.
  sleep_between_reads
}

skip_sensitive_section() {
  local id="$1" title="$2"
  sections=$((sections + 1))
  skipped=$((skipped + 1))
  printf '\n[%02d/49] %s\n[PARTIAL] Collection disabled by data-minimization policy.\n' "$id" "$title" | emit
}

report_eks_applicability() {
  local aws_nodes
  aws_nodes="$(kubectl --request-timeout="$TIMEOUT" get nodes -o json 2>/dev/null | jq '[.items[] | select((.spec.providerID // "") | startswith("aws"))] | length' 2>/dev/null || printf 0)"
  if [[ "$aws_nodes" == 0 ]]; then
    printf '[N/A] AWS/EKS-only checks — node providerID is not AWS; Kubernetes topology remains assessed.\n' | emit
    not_applicable=$((not_applicable + 1))
  else
    printf '[INFO] AWS/EKS provider detected on %s node(s).\n' "$aws_nodes" | emit
  fi
}
section_json_summary() {
  local id="$1" title="$2" jq_filter="$3"; shift 3
  local file="" result status=0
  sections=$((sections + 1))
  printf '\n[%02d/49] %s\n' "$id" "$title" | emit
  set +e
  result="$(kubectl --request-timeout="$TIMEOUT" "$@" -o json 2>&1 | jq -r "$jq_filter" 2>&1 | head -n "$MAX_LINES")"; status=${PIPESTATUS[0]}
  set -e
  (( status == 141 )) && status=0
  if ((status == 0)); then
    printf '%s\n' "$result" | emit; succeeded=$((succeeded + 1))
  elif grep -qiE 'the server could not find the requested resource|doesn.t have a resource type|not found|no matches for kind' <<<"$result"; then
    printf '[N/A] Optional API or resource is not installed.\n' | emit
    not_applicable=$((not_applicable + 1))
  else
    printf '[UNAVAILABLE] %s\n' "$result" | emit; failed=$((failed + 1))
  fi
  [[ -n "$OUTPUT_DIR" ]] && { file="$OUTPUT_DIR/$(printf '%02d' "$id")-$(safe_name "$title").txt"; printf '%s\n' "$result" > "$file"; }
  sleep_between_reads
}

printf 'EKS / Kubernetes discovery — read-only\nGenerated: %s\nScope: %s\n' "$(date -Is)" "${NAMESPACE:-all namespaces}" | emit
printf 'Guardrails: timeout=%s delay=%sms max-lines=%s secrets=not-collected configmap-values=not-collected\n' "$TIMEOUT" "$DELAY_MS" "$MAX_LINES" | emit

# Cluster and control-plane posture.
run_section 1 'Version and API health' version
run_section 2 'Cluster information' cluster-info
report_eks_applicability
run_section 3 'Nodes' get nodes -o wide
section_json_summary 4 'Node conditions' '[.items[] | {name:.metadata.name, conditions:[.status.conditions[] | select(.status != "False") | {type,status,reason,message}]}]' get nodes
run_section 5 'Namespaces' get namespaces
run_section 6 'Resource quotas' get resourcequota "${scope[@]}"
run_section 7 'Limit ranges' get limitrange "${scope[@]}"
run_section 8 'Priority classes' get priorityclass
run_section 9 'Runtime classes' get runtimeclass

# Workloads and lifecycle.
run_section 10 'Deployments' get deployments "${scope[@]}" -o wide
run_section 11 'StatefulSets' get statefulsets "${scope[@]}"
run_section 12 'DaemonSets' get daemonsets "${scope[@]}"
run_section 13 'ReplicaSets' get replicasets "${scope[@]}"
run_section 14 'Pods' get pods "${scope[@]}" -o wide
section_json_summary 15 'Problem pods' '[.items[] | select(.status.phase != "Running" and .status.phase != "Succeeded") | {namespace:.metadata.namespace,name:.metadata.name,phase:.status.phase,reason:.status.reason}]' get pods "${scope[@]}"
run_section 16 'Jobs' get jobs "${scope[@]}"
run_section 17 'CronJobs' get cronjobs "${scope[@]}"
run_section 18 'Horizontal Pod Autoscalers' get hpa "${scope[@]}"
run_section 19 'Pod Disruption Budgets' get pdb "${scope[@]}"
run_section 20 'Vertical Pod Autoscalers' get vpa "${scope[@]}"
run_section 21 'KEDA ScaledObjects' get scaledobject "${scope[@]}"

# Network, traffic and service mesh.
run_section 22 'Services' get services "${scope[@]}"
run_section 23 'EndpointSlices' get endpointslices "${scope[@]}"
run_section 24 'Ingresses' get ingress "${scope[@]}"
run_section 25 'Gateway API objects' get gateway,httproute,grpcroute,tlsroute "${scope[@]}"
run_section 26 'NetworkPolicies' get networkpolicy "${scope[@]}"
run_section 27 'Ingress controller topology' get deployment,daemonset,service "${scope[@]}" -l app.kubernetes.io/component=controller
run_section 28 'Istio control plane' get deployment,service "${scope[@]}" -l app=istiod
run_section 29 'Istio traffic and mTLS' get virtualservice,destinationrule,peerauthentication,authorizationpolicy -A
run_section 30 'CNI health (Calico)' get tigerastatus

# Storage and configuration. Secrets are intentionally metadata only.
run_section 31 'Storage classes' get storageclass
run_section 32 'Persistent volumes' get pv
run_section 33 'Persistent volume claims' get pvc "${scope[@]}"
run_section 34 'Volume snapshots' get volumesnapshot "${scope[@]}"
skip_sensitive_section 35 'ConfigMaps metadata'
skip_sensitive_section 36 'Secrets metadata'

# Identity, governance, observability and GitOps.
run_section 37 'Service accounts' get serviceaccount "${scope[@]}"
run_section 38 'Roles and RoleBindings' get role,rolebinding "${scope[@]}"
run_section 39 'ClusterRoles and ClusterRoleBindings' get clusterrole,clusterrolebinding
run_section 40 'Custom resource definitions' get crd
run_section 41 'Admission webhooks' get validatingwebhookconfiguration,mutatingwebhookconfiguration
run_section 42 'Kyverno policies' get clusterpolicy,policy "${scope[@]}"
run_section 43 'Argo CD Applications' get applications "${scope[@]}"
run_section 44 'Argo CD application health' get applications "${scope[@]}" -o custom-columns=NAMESPACE:.metadata.namespace,NAME:.metadata.name,SYNC:.status.sync.status,HEALTH:.status.health.status,REVISION:.status.sync.revision
run_section 45 'Prometheus stack' get deployment,statefulset,service "${scope[@]}" -l app.kubernetes.io/name=prometheus
run_section 46 'Prometheus discovery objects' get servicemonitor,podmonitor,prometheusrule "${scope[@]}"
run_section 47 'Metrics APIs' get --raw /apis/metrics.k8s.io/v1beta1/nodes
event_filter="[.items[] | select((.eventTime // .lastTimestamp // .metadata.creationTimestamp // \"\") >= \"$EVENT_CUTOFF\") | {namespace:.metadata.namespace,name:.metadata.name,type:.type,reason:.reason,eventTime:(.eventTime // .lastTimestamp // .metadata.creationTimestamp),regarding:(.regarding // .involvedObject | {kind,name,namespace})}]"
section_json_summary 48 "Warnings and events (lookback: $SINCE)" "$event_filter" get events "${scope[@]}" --field-selector type=Warning
run_section 49 'API services' get apiservice

summary="Discovery summary: sections=$sections succeeded=$succeeded n/a=$not_applicable partial=$skipped unavailable=$failed"
printf '\n%s\n' "$summary" | emit
if [[ -n "$OUTPUT_DIR" ]]; then
  jq -n --arg generated "$(date -Is)" --arg scope "${NAMESPACE:-all}" --arg timeout "$TIMEOUT" --argjson sections "$sections" --argjson succeeded "$succeeded" --argjson not_applicable "$not_applicable" --argjson partial "$skipped" --argjson unavailable "$failed" '{generated_at:$generated,scope:$scope,read_only:true,secrets:"not-collected",configMapValues:"not-collected",timeout:$timeout,sections:$sections,succeeded:$succeeded,not_applicable:$not_applicable,partial:$partial,unavailable:$unavailable}' > "$OUTPUT_DIR/summary.json"
  printf 'Output: %s\n' "$OUTPUT_DIR" | emit
fi
(( failed == 0 )) || exit 1
