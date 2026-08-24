#!/usr/bin/env bash
set -euo pipefail

INGRESS_URL="${INGRESS_URL:-https://192.168.109.151:31882}"
HOST_HEADER="${HOST_HEADER:-nginx.lab.local}"
PASSWORD="${BANKING_E2E_PASSWORD:-MouraPixLab2026!}"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
fixture_script="${script_dir}/banking-e2e-fixture.sh"
PROMETHEUS_NAMESPACE="${PROMETHEUS_NAMESPACE:-monitoring}"
PROMETHEUS_SERVICE="${PROMETHEUS_SERVICE:-kube-prometheus-stack-prometheus}"

authority="${INGRESS_URL#https://}"
ip="${authority%%:*}"
port="${authority##*:}"
base_url="https://${HOST_HEADER}:${port}/bank"
resolve=(--resolve "${HOST_HEADER}:${port}:${ip}")
work_dir="$(mktemp -d)"

new_uuid() {
  if [[ -r /proc/sys/kernel/random/uuid ]]; then
    tr '[:upper:]' '[:lower:]' </proc/sys/kernel/random/uuid
  elif command -v uuidgen >/dev/null; then
    uuidgen | tr '[:upper:]' '[:lower:]'
  elif command -v powershell.exe >/dev/null; then
    powershell.exe -NoProfile -Command '[guid]::NewGuid().ToString()' | tr -d '\r' | tr '[:upper:]' '[:lower:]'
  else
    echo 'ERRO: não foi possível gerar UUID' >&2
    exit 1
  fi
}

transaction_id="$(new_uuid)"
reversal_id="$(new_uuid)"
run_id="$(new_uuid)"
sender_cpf="$(bash "${fixture_script}" cpf "${run_id}" 3)"
receiver_cpf="$(bash "${fixture_script}" cpf "${run_id}" 4)"

cleanup() {
  local status=$? cleanup_failed=0
  trap - EXIT
  set +e
  if ! bash "${fixture_script}" cleanup "${run_id}" >/dev/null 2>&1; then
    cleanup_failed=1
    echo 'ERRO: a fixture não conseguiu remover os dados temporários do teste PIX' >&2
  fi
  if [[ -n "${work_dir}" && -d "${work_dir}" && "${work_dir}" == /tmp/* ]]; then
    rm -rf -- "${work_dir}"
  else
    cleanup_failed=1
    echo 'ERRO: recusa ao remover um diretório temporário inesperado' >&2
  fi
  if [[ "${status}" -eq 0 && "${cleanup_failed}" -ne 0 ]]; then
    status=1
  fi
  exit "${status}"
}
trap cleanup EXIT

require_cmd() { command -v "$1" >/dev/null || { echo "ERRO: comando obrigatório ausente: $1" >&2; exit 1; }; }
for command in kubectl curl jq base64; do require_cmd "${command}"; done
[[ -r "${fixture_script}" ]] || { echo "ERRO: fixture ausente: ${fixture_script}" >&2; exit 1; }

kubectl -n banking rollout status deployment/account-service deployment/transaction-service --timeout=240s
bash "${fixture_script}" wait

register() {
  local role="$1" cpf="$2" cookie="$3" output="$4" payload status
  payload="$(jq -nc --arg owner "E2E:${run_id}:pix-${role}" --arg cpf "${cpf}" --arg password "${PASSWORD}" \
    '{ownerName:$owner,cpf:$cpf,password:$password}')"
  status="$(printf '%s' "${payload}" | curl -ksS "${resolve[@]}" -c "${cookie}" -o "${output}" -w '%{http_code}' \
    -H 'Content-Type: application/json' --data-binary @- "${base_url}/auth/register")"
  [[ "${status}" == "201" ]] || { echo "ERRO: registro ${role} retornou HTTP ${status}" >&2; cat "${output}" >&2; exit 1; }
  jq -e '.id and (.accountNumber | test("^[0-9]{8}$"))' "${output}" >/dev/null
}

register sender "${sender_cpf}" "${work_dir}/sender.cookies" "${work_dir}/sender.json"
register receiver "${receiver_cpf}" "${work_dir}/receiver.cookies" "${work_dir}/receiver.json"
unset sender_cpf receiver_cpf
sender_id="$(jq -r .id "${work_dir}/sender.json")"
receiver_id="$(jq -r .id "${work_dir}/receiver.json")"

pix_status="$(curl -ksS "${resolve[@]}" -b "${work_dir}/receiver.cookies" -o "${work_dir}/pix.json" -w '%{http_code}' \
  -X PUT "${base_url}/accounts/me/pix-key")"
[[ "${pix_status}" == "200" ]] || { echo "ERRO: criação da chave PIX retornou HTTP ${pix_status}" >&2; exit 1; }
pix_key="$(jq -er .pixKey "${work_dir}/pix.json")"

bash "${fixture_script}" fund "${run_id}" "${sender_id}" 100.00 >/dev/null

transfer_payload="$(jq -nc --arg source "${sender_id}" --arg pix "${pix_key}" --arg password "${PASSWORD}" \
  '{sourceAccountId:$source,pixKey:$pix,amount:0.01,description:"PIX E2E automatizado",password:$password}')"
transfer() {
  curl -ksS "${resolve[@]}" -b "${work_dir}/sender.cookies" -o "$1" -w '%{http_code}' \
    -H 'Content-Type: application/json' -H "Idempotency-Key: ${transaction_id}" \
    --data-binary "${transfer_payload}" "${base_url}/transactions"
}
[[ "$(transfer "${work_dir}/transfer.json")" == "200" ]]
[[ "$(transfer "${work_dir}/retry.json")" == "200" ]]
jq -e --arg id "${transaction_id}" '.id==$id and .status=="COMPLETED"' "${work_dir}/transfer.json" >/dev/null
jq -e --slurpfile first "${work_dir}/transfer.json" '.id==$first[0].id' "${work_dir}/retry.json" >/dev/null

curl -ksS "${resolve[@]}" -b "${work_dir}/sender.cookies" "${base_url}/accounts/me" >"${work_dir}/sender-after-pix.json"
curl -ksS "${resolve[@]}" -b "${work_dir}/receiver.cookies" "${base_url}/accounts/me" >"${work_dir}/receiver-after-pix.json"
jq -e '.balance==99.99' "${work_dir}/sender-after-pix.json" >/dev/null
jq -e '.balance==0.01' "${work_dir}/receiver-after-pix.json" >/dev/null

reversal_payload="$(jq -nc --arg id "${reversal_id}" '{reversalId:$id}')"
reversal_base64="$(printf '%s' "${reversal_payload}" | base64 | tr -d '\n')"
reversal_url="http://account-service/internal/v1/transfers/${transaction_id}/reversals"
transaction_pod="$(kubectl -n banking get pod -l app=transaction-service -o jsonpath='{.items[0].metadata.name}')"
# The positional parameters and temporary path must expand inside the container shell.
# shellcheck disable=SC2016
kubectl -n banking exec "${transaction_pod}" -c transaction-service -- sh -c \
  'tmp="/tmp/pix-reversal-$$.json"; echo "$1" | base64 -d >"$tmp"; wget -qO- --header Content-Type:application/json --post-file "$tmp" "$2"; code=$?; rm -f "$tmp"; exit $code' \
  _ "${reversal_base64}" "${reversal_url}" >"${work_dir}/reversal.json"
jq -e --arg id "${reversal_id}" '.transactionId==$id and .status=="COMPLETED"' "${work_dir}/reversal.json" >/dev/null

curl -ksS "${resolve[@]}" -b "${work_dir}/sender.cookies" "${base_url}/accounts/me" >"${work_dir}/sender-final.json"
curl -ksS "${resolve[@]}" -b "${work_dir}/receiver.cookies" "${base_url}/accounts/me" >"${work_dir}/receiver-final.json"
jq -e '.balance==100.00' "${work_dir}/sender-final.json" >/dev/null
jq -e '.balance==0.00' "${work_dir}/receiver-final.json" >/dev/null

prometheus_query() {
  local encoded
  encoded="$(jq -rn --arg query "$1" '$query|@uri')"
  kubectl get --raw "/api/v1/namespaces/${PROMETHEUS_NAMESPACE}/services/http:${PROMETHEUS_SERVICE}:9090/proxy/api/v1/query?query=${encoded}"
}
for attempt in {1..12}; do
  transfers="$(prometheus_query 'banking_pix_transfers_total{outcome="completed"}')"
  reversals="$(prometheus_query 'banking_pix_reversals_completed_total')"
  if jq -e '.data.result | length > 0' <<<"${transfers}" >/dev/null && \
     jq -e '.data.result | length > 0' <<<"${reversals}" >/dev/null; then
    break
  fi
  [[ "${attempt}" -lt 12 ]] || { echo 'ERRO: métricas PIX não apareceram no Prometheus' >&2; exit 1; }
  sleep 5
done

echo 'Moura Banking PIX: transferência, idempotência, estorno, saldos e métricas validados.'
