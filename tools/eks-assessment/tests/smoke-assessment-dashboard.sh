#!/usr/bin/env bash
# Read-only smoke test for the server-rendered assessment dashboard.
set -euo pipefail

TOOL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPOSITORY_ROOT="$(cd "$TOOL_ROOT/../.." && pwd)"
BASE_URL="${ASSESSMENT_BASE_URL:-http://127.0.0.1:8765}"
ROOT="${ASSESSMENT_ROOT:-$REPOSITORY_ROOT/assessment}"
COLLECTION="${ASSESSMENT_COLLECTION:-}"

while (($#)); do
  case "$1" in
    --base-url) BASE_URL="$2"; shift 2 ;;
    --root) ROOT="$2"; shift 2 ;;
    --collection) COLLECTION="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

command -v curl >/dev/null || { echo "curl is required" >&2; exit 2; }
command -v jq >/dev/null || { echo "jq is required" >&2; exit 2; }

if [[ -z "$COLLECTION" ]]; then
  COLLECTION="$(find "$ROOT" -mindepth 1 -maxdepth 1 -type d -name 'eks-*' -printf '%T@ %f\n' | sort -nr | awk 'NR==1{print $2}')"
fi
[[ "$COLLECTION" =~ ^[A-Za-z0-9._-]+$ ]] || { echo "invalid collection id" >&2; exit 2; }
DIR="$ROOT/$COLLECTION"
[[ -d "$DIR" ]] || { echo "collection not found: $DIR" >&2; exit 2; }

health="$(curl -fsS "$BASE_URL/api/health")"
jq -e '.ok == true and .readOnly == true' >/dev/null <<<"$health"

collection_status="$(curl -fsS "$BASE_URL/api/collection-status")"
jq -e '.active == false and (.status | type == "string")' >/dev/null <<<"$collection_status"

paths=(
  "/styles.css"
  "/api/collection-status"
  "/?collection=$COLLECTION"
  "/assessment?collection=$COLLECTION"
  "/problems?collection=$COLLECTION"
  "/resources?collection=$COLLECTION&kind=nodes"
  "/resources?collection=$COLLECTION&kind=pods"
  "/resources?collection=$COLLECTION&kind=deployments"
  "/resources?collection=$COLLECTION&kind=statefulsets"
  "/resources?collection=$COLLECTION&kind=daemonsets"
  "/resources?collection=$COLLECTION&kind=rabbitmq"
  "/technologies?collection=$COLLECTION"
  "/capacity?collection=$COLLECTION"
  "/prometheus?collection=$COLLECTION"
  "/aws?collection=$COLLECTION"
  "/cis-security?collection=$COLLECTION"
  "/coverage?collection=$COLLECTION"
  "/compare?collection=$COLLECTION"
  "/export?collection=$COLLECTION"
  "/manifests?collection=$COLLECTION"
)
for path in "${paths[@]}"; do
  code="$(curl -sS -o /dev/null -w '%{http_code}' "$BASE_URL$path")"
  [[ "$code" == "200" ]] || { echo "HTTP $code: $path" >&2; exit 1; }
done

stylesheet="$(curl -fsS "$BASE_URL/styles.css")"
grep -Fq ':root {' <<<"$stylesheet" || { echo "dashboard stylesheet is empty or invalid" >&2; exit 1; }

assert_all_names(){
  local kind="$1" file="$2" filter="$3" page name
  page="$(curl -fsS "$BASE_URL/resources?collection=$COLLECTION&kind=$kind")"
  while IFS= read -r name; do
    [[ -z "$name" ]] || grep -Fq -- "$name" <<<"$page" || { echo "$kind missing from page: $name" >&2; exit 1; }
  done < <(jq -r "$filter" "$file")
}
assert_all_names nodes "$DIR/nodes.json" '.items[].metadata.name'
assert_all_names pods "$DIR/pods.json" '.items[].metadata.name'
assert_all_names deployments "$DIR/workloads.json" '.items[] | select(.kind=="Deployment") | .metadata.name'
assert_all_names statefulsets "$DIR/workloads.json" '.items[] | select(.kind=="StatefulSet") | .metadata.name'
assert_all_names daemonsets "$DIR/workloads.json" '.items[] | select(.kind=="DaemonSet") | .metadata.name'

first_node="$(jq -r '.items[0].metadata.name // empty' "$DIR/nodes.json")"
first_deployment="$(jq -r '.items[] | select(.kind=="Deployment") | .metadata.name' "$DIR/workloads.json" | head -1)"

read -r workload_namespace workload_kind workload_name < <(
  jq -r '.workloads[] | select(.kind=="Deployment") | [.namespace,.kind,.name] | @tsv' "$DIR/comprehensive-assessment.json" | head -1
)
if [[ -n "${workload_name:-}" ]]; then
  curl -fsS "$BASE_URL/workload?collection=$COLLECTION&namespace=$workload_namespace&kind=$workload_kind&name=$workload_name" | grep -Fq -- "$workload_name"
fi

capacity="$(jq -r '.summary.capacityRecommendations // 0' "$DIR/comprehensive-assessment.json")"
if ((capacity > 0)); then
  curl -fsS "$BASE_URL/capacity?collection=$COLLECTION" | grep -Fq 'CPU req proposta'
fi

prometheus_page="$(curl -fsS "$BASE_URL/prometheus?collection=$COLLECTION")"
grep -Fq 'Prometheus — visão operacional' <<<"$prometheus_page"
grep -Fq 'CPU / request' <<<"$prometheus_page"
grep -Fq 'Exibir métricas técnicas e percentis completos' <<<"$prometheus_page"
grep -Fq 'Runtime e tuning descobertos automaticamente' <<<"$prometheus_page"
grep -Fq 'Saúde do Prometheus' <<<"$prometheus_page"
grep -Fq 'Sinais simplificados e tecnologias' <<<"$prometheus_page"

aws_page="$(curl -fsS "$BASE_URL/aws?collection=$COLLECTION")"
grep -Fq 'AWS / Amazon EKS' <<<"$aws_page"
grep -Fq 'Permissão ausente vira UNKNOWN' <<<"$aws_page"

cis_page="$(curl -fsS "$BASE_URL/cis-security?collection=$COLLECTION")"
grep -Fq 'CIS Security' <<<"$cis_page"
if [[ -f "$DIR/cis-security-assessment.json" ]]; then
  grep -Fq 'Não representa certificação nem compliance integral' <<<"$cis_page"
fi

collect_page="$(curl -fsS "$BASE_URL/collect")"
grep -Fq 'máximo 30 min' <<<"$collect_page"
grep -Fq 'class="checkbox-row"' <<<"$collect_page"
grep -Fq 'type="checkbox" name="account_security"' <<<"$collect_page"
grep -Fq 'id="collection-progress"' <<<"$collect_page"
grep -Fq 'role="progressbar"' <<<"$collect_page"
grep -Fq 'X-Assessment-Async' <<<"$collect_page"
grep -Fq 'progressPercent' <<<"$collect_page"
unauthorized_code="$(curl -sS -o /dev/null -w '%{http_code}' -X POST "$BASE_URL/collect")"
[[ "$unauthorized_code" == "403" ]] || { echo "unauthenticated collection was not rejected" >&2; exit 1; }

runtime_count="$(jq '[.workloads[] | select((.runtimeDetected // []) | length > 0)] | length' "$DIR/prometheus-telemetry.json")"
if ((runtime_count > 0)); then
  while IFS= read -r deployment; do
    [[ -z "$deployment" ]] || grep -Fq -- "$deployment" <<<"$prometheus_page" || {
      echo "runtime deployment missing from Prometheus page: $deployment" >&2
      exit 1
    }
  done < <(jq -r '.workloads[] | select((.runtimeDetected // []) | length > 0) | .deployment' "$DIR/prometheus-telemetry.json")

  while IFS= read -r option; do
    [[ -z "$option" ]] || grep -Fq -- "$option" <<<"$prometheus_page" || {
      echo "runtime option missing from Prometheus page: $option" >&2
      exit 1
    }
  done < <(jq -r '.workloads[].runtimeConfig[]?.name' "$DIR/prometheus-telemetry.json")
fi

curl -fsS "$BASE_URL/export?collection=$COLLECTION" | jq -e '.summary.workloads > 0' >/dev/null
curl -fsS "$BASE_URL/manifests?collection=$COLLECTION" | jq -e '.items | type == "array"' >/dev/null

jq -n --arg collection "$COLLECTION" --arg baseUrl "$BASE_URL" \
  --arg node "$first_node" --arg deployment "$first_deployment" \
  --argjson routes "${#paths[@]}" \
  '{ok:true,collection:$collection,baseUrl:$baseUrl,routes:$routes,node:$node,deployment:$deployment}'
