#!/usr/bin/env bash
set -euo pipefail

INGRESS_URL="${INGRESS_URL:-https://192.168.109.151:31882}"
HOST_HEADER="${HOST_HEADER:-nginx.lab.local}"
PASSWORD="${BANKING_E2E_PASSWORD:-MouraLab2026!}"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
fixture_script="${script_dir}/banking-e2e-fixture.sh"
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

run_id="$(new_uuid)"
source_cpf="$(bash "${fixture_script}" cpf "${run_id}" 1)"
destination_cpf="$(bash "${fixture_script}" cpf "${run_id}" 2)"
bad_transaction_id="$(new_uuid)"
transaction_id="$(new_uuid)"

cleanup() {
  local status=$? cleanup_failed=0
  trap - EXIT
  set +e
  if ! bash "${fixture_script}" cleanup "${run_id}" >/dev/null 2>&1; then
    cleanup_failed=1
    echo 'ERRO: a fixture não conseguiu remover os dados temporários do teste Web' >&2
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
for command in kubectl curl jq; do require_cmd "${command}"; done
[[ -r "${fixture_script}" ]] || { echo "ERRO: fixture ausente: ${fixture_script}" >&2; exit 1; }

kubectl -n banking rollout status deployment/banking-web deployment/account-service deployment/transaction-service --timeout=240s
bash "${fixture_script}" wait
html="$(curl -ksS "${resolve[@]}" "https://${HOST_HEADER}:${port}/banking/")"
grep -q '<title>Moura Banking</title>' <<<"${html}"
status="$(curl -ksS -o /dev/null -w '%{http_code}' "${resolve[@]}" "${base_url}/accounts/me")"
[[ "${status}" == "401" ]]

register() {
  local owner="$1" cpf="$2" cookie="$3" output="$4" payload status
  payload="$(jq -nc --arg owner "${owner}" --arg cpf "${cpf}" --arg password "${PASSWORD}" \
    '{ownerName:$owner,cpf:$cpf,password:$password}')"
  status="$(printf '%s' "${payload}" | curl -ksS "${resolve[@]}" -c "${cookie}" -o "${output}" -w '%{http_code}' \
    -H 'Content-Type: application/json' --data-binary @- "${base_url}/auth/register")"
  [[ "${status}" == "201" ]] || { echo "ERRO: registro retornou HTTP ${status}" >&2; cat "${output}" >&2; exit 1; }
  jq -e '.accountNumber | test("^[0-9]{8}$")' "${output}" >/dev/null
}

source_owner="E2E:${run_id}:web-source:José Gonçalves"
destination_owner="E2E:${run_id}:web-destination:Maria da Conceição"
register "${source_owner}" "${source_cpf}" "${work_dir}/source.cookies" "${work_dir}/source.json"
register "${destination_owner}" "${destination_cpf}" "${work_dir}/destination.cookies" "${work_dir}/destination.json"
unset source_cpf destination_cpf
jq -e --arg owner "${source_owner}" '.ownerName == $owner' "${work_dir}/source.json" >/dev/null
jq -e --arg owner "${destination_owner}" '.ownerName == $owner' "${work_dir}/destination.json" >/dev/null
source_id="$(jq -r .id "${work_dir}/source.json")"
destination_id="$(jq -r .id "${work_dir}/destination.json")"

bash "${fixture_script}" fund "${run_id}" "${source_id}" 100.00 >/dev/null

bad_payload="$(jq -nc --arg source "${source_id}" --arg destination "${destination_id}" \
  '{sourceAccountId:$source,destinationAccountId:$destination,amount:1.25,description:"Senha incorreta",password:"SenhaErrada2026!"}')"
bad_status="$(curl -ksS "${resolve[@]}" -b "${work_dir}/source.cookies" -o "${work_dir}/bad-transaction.json" -w '%{http_code}' \
  -H 'Content-Type: application/json' -H "Idempotency-Key: ${bad_transaction_id}" \
  --data-binary "${bad_payload}" "${base_url}/transactions")"
[[ "${bad_status}" == "401" ]]

payload="$(jq -nc --arg source "${source_id}" --arg destination "${destination_id}" --arg password "${PASSWORD}" \
  '{sourceAccountId:$source,destinationAccountId:$destination,amount:1.25,description:"E2E Moura Banking",password:$password}')"
status="$(curl -ksS "${resolve[@]}" -b "${work_dir}/source.cookies" -o "${work_dir}/transaction.json" -w '%{http_code}' \
  -H 'Content-Type: application/json' -H "Idempotency-Key: ${transaction_id}" \
  --data-binary "${payload}" "${base_url}/transactions")"
[[ "${status}" == "200" ]]
jq -e --arg id "${transaction_id}" '.id==$id and .status=="COMPLETED"' "${work_dir}/transaction.json" >/dev/null

curl -ksS "${resolve[@]}" -b "${work_dir}/source.cookies" \
  "${base_url}/transactions?sourceAccountId=${source_id}" >"${work_dir}/statement.json"
jq -e 'length >= 1 and .[0].description == "E2E Moura Banking"' "${work_dir}/statement.json" >/dev/null
curl -ksS "${resolve[@]}" -b "${work_dir}/destination.cookies" \
  "${base_url}/transactions?sourceAccountId=${destination_id}" >"${work_dir}/incoming.json"
jq -e --arg id "${destination_id}" 'length >= 1 and .[0].destinationAccountId==$id' "${work_dir}/incoming.json" >/dev/null

echo 'Moura Banking: autenticação, isolamento, UTF-8, transferência e limpeza validados.'
