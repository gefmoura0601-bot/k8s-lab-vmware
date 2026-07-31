#!/usr/bin/env bash
set -euo pipefail

namespace="${NAMESPACE:-banking}"
duration="${TRACE_DURATION_SECONDS:-60}"
output_dir="${OUTPUT_DIR:-./diagnostics}"
local_port="${DOTNET_MONITOR_PORT:-52323}"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
pod="$(kubectl get pod -n "$namespace" -l app=transaction-service \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}')"

if [[ -z "$pod" ]]; then
  echo "No running transaction-service pod found in namespace $namespace" >&2
  exit 1
fi

mkdir -p "$output_dir"
kubectl port-forward -n "$namespace" "pod/$pod" \
  "$local_port:52323" >"$output_dir/dotnet-monitor-port-forward.log" 2>&1 &
forward_pid=$!
trap 'kill "$forward_pid" 2>/dev/null || true' EXIT

for _ in {1..20}; do
  if curl --fail --silent "http://127.0.0.1:$local_port/processes" >/dev/null; then
    break
  fi
  sleep 0.5
done

curl --fail --show-error --silent \
  "http://127.0.0.1:$local_port/trace?profile=Cpu&durationSeconds=$duration" \
  --output "$output_dir/transaction-service-cpu-${timestamp}.nettrace"
curl --fail --show-error --silent \
  "http://127.0.0.1:$local_port/gcdump" \
  --output "$output_dir/transaction-service-${timestamp}.gcdump"

echo "Created:"
echo "  $output_dir/transaction-service-cpu-${timestamp}.nettrace"
echo "  $output_dir/transaction-service-${timestamp}.gcdump"
