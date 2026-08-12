#!/usr/bin/env bash
set -euo pipefail

TOP_COUNT="${TOP_COUNT:-15}"

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Comando obrigatório não encontrado: $1" >&2
    exit 1
  }
}

require_cmd kubectl
require_cmd jq
require_cmd awk
require_cmd sort

section() {
  printf '\n=== %s ===\n' "$1"
}

section "Memória dos nós"
kubectl top nodes

section "Capacidade e memória alocável"
kubectl get nodes -o custom-columns='NODE:.metadata.name,CAPACITY:.status.capacity.memory,ALLOCATABLE:.status.allocatable.memory'

section "Condição MemoryPressure"
kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{range .status.conditions[?(@.type=="MemoryPressure")]}{.status}{"\t"}{.reason}{end}{"\n"}{end}'

section "Top ${TOP_COUNT} containers por uso atual"
kubectl top pods -A --containers --sort-by=memory | awk -v count="${TOP_COUNT}" 'NR == 1 || NR <= count + 1'

section "Requests e limits de memória por namespace (MiB)"
kubectl get pods -A -o json | jq -r '
  def bytes:
    if . == null or . == "" then 0
    elif test("Ki$") then sub("Ki$"; "") | tonumber * 1024
    elif test("Mi$") then sub("Mi$"; "") | tonumber * 1048576
    elif test("Gi$") then sub("Gi$"; "") | tonumber * 1073741824
    elif test("Ti$") then sub("Ti$"; "") | tonumber * 1099511627776
    elif test("K$") then sub("K$"; "") | tonumber * 1000
    elif test("M$") then sub("M$"; "") | tonumber * 1000000
    elif test("G$") then sub("G$"; "") | tonumber * 1000000000
    else tonumber
    end;
  [.items[] as $pod
   | $pod.spec.containers[]
   | {namespace: $pod.metadata.namespace,
      request: ((.resources.requests.memory // "0") | bytes),
      limit: ((.resources.limits.memory // "0") | bytes)}]
  | group_by(.namespace)
  | map({namespace: .[0].namespace,
         requestMiB: ((map(.request) | add) / 1048576 | round),
         limitMiB: ((map(.limit) | add) / 1048576 | round)})
  | sort_by(-.requestMiB)
  | (["NAMESPACE","REQUEST_MiB","LIMIT_MiB"], (.[] | [.namespace, (.requestMiB|tostring), (.limitMiB|tostring)]))
  | @tsv
'

section "Containers atualmente ou anteriormente OOMKilled"
kubectl get pods -A -o json | jq -r '
  [.items[] as $pod
   | ($pod.status.containerStatuses // [])[]
   | select(.state.terminated.reason == "OOMKilled" or .lastState.terminated.reason == "OOMKilled")
   | [$pod.metadata.namespace, $pod.metadata.name, .name, (.restartCount | tostring)]]
  | if length == 0 then "nenhum OOMKilled encontrado"
    else ((["NAMESPACE","POD","CONTAINER","RESTARTS"], .[]) | @tsv)
    end
'

section "Recomendações VPA"
kubectl get vpa -A

section "Resumo"
echo "Consulte o dashboard Grafana: Kubernetes / Memory Health"
echo "Atenção: uso atual não substitui análise histórica; compare com requests, limits, OOMKilled e recomendações VPA."