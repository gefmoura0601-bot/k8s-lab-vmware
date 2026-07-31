#!/usr/bin/env bash
set -euo pipefail

namespace="${NAMESPACE:-banking}"
duration="${JFR_DURATION:-60s}"
output_dir="${OUTPUT_DIR:-./diagnostics}"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
pod="$(kubectl get pod -n "$namespace" -l app=account-service \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}')"

if [[ -z "$pod" ]]; then
  echo "No running account-service pod found in namespace $namespace" >&2
  exit 1
fi

mkdir -p "$output_dir"
remote_jfr="/diagnostics/account-service-profile-${timestamp}.jfr"

kubectl exec -n "$namespace" "$pod" -c account-service -- \
  env -u JAVA_TOOL_OPTIONS jcmd 1 JFR.start name=on-demand-profile settings=profile \
    "duration=$duration" "filename=$remote_jfr"
kubectl exec -n "$namespace" "$pod" -c account-service -- \
  env -u JAVA_TOOL_OPTIONS jcmd 1 VM.native_memory summary scale=MB \
  >"$output_dir/account-service-nmt-${timestamp}.txt"

echo "JFR is recording for $duration. Copy it after the recording finishes:"
echo "kubectl cp -n $namespace -c account-service $pod:$remote_jfr $output_dir/account-service-profile-${timestamp}.jfr"
