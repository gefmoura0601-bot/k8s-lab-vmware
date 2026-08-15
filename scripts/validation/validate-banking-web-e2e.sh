#!/usr/bin/env bash
set -euo pipefail

INGRESS_URL="${INGRESS_URL:-https://192.168.109.151:31882}"
HOST_HEADER="${HOST_HEADER:-nginx.lab.local}"
PASSWORD="${BANKING_E2E_PASSWORD:-MouraLab2026!}"
authority="${INGRESS_URL#https://}"
ip="${authority%%:*}"
port="${authority##*:}"
base_url="https://${HOST_HEADER}:${port}/bank"
resolve=(--resolve "${HOST_HEADER}:${port}:${ip}")
work_dir="$(mktemp -d)"
source_id=""
destination_id=""

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

funding_journal_id="$(new_uuid)"
bad_transaction_id="$(new_uuid)"
transaction_id="$(new_uuid)"

cleanup() {
  status=$?
  set +e
  if [[ -n "${source_id}" && -n "${destination_id}" ]]; then
    kubectl -n databases exec statefulset/postgres -- psql -U appuser -d appdb -v ON_ERROR_STOP=1 -c \
      "BEGIN;
       DELETE FROM transaction_service.transactions WHERE id IN ('${transaction_id}'::uuid,'${bad_transaction_id}'::uuid);
       DELETE FROM account_service.ledger_entries WHERE journal_id IN ('${transaction_id}'::uuid,'${bad_transaction_id}'::uuid,'${funding_journal_id}'::uuid);
       DELETE FROM account_service.processed_transfers WHERE transaction_id IN ('${transaction_id}'::uuid,'${bad_transaction_id}'::uuid);
       DELETE FROM account_service.pix_keys WHERE account_id IN ('${source_id}'::uuid,'${destination_id}'::uuid);
       DELETE FROM account_service.accounts WHERE id IN ('${source_id}'::uuid,'${destination_id}'::uuid);
       COMMIT;" >/dev/null
  fi
  rm -rf -- "${work_dir}"
  exit "${status}"
}
trap cleanup EXIT

require_cmd() { command -v "$1" >/dev/null || { echo "ERRO: comando obrigatório ausente: $1" >&2; exit 1; }; }
for command in kubectl curl jq; do require_cmd "${command}"; done

kubectl -n banking rollout status deployment/banking-web deployment/account-service deployment/transaction-service --timeout=240s
html="$(curl -ksS "${resolve[@]}" "https://${HOST_HEADER}:${port}/banking/")"
grep -q '<title>Moura Banking</title>' <<<"${html}"
status="$(curl -ksS -o /dev/null -w '%{http_code}' "${resolve[@]}" "${base_url}/accounts/me")"
[[ "${status}" == "401" ]]

register() {
  local owner="$1" cookie="$2" output="$3" payload status
  payload="$(jq -nc --arg owner "${owner} $(date +%s)-${RANDOM}" --arg password "${PASSWORD}" \
    '{ownerName:$owner,password:$password}')"
  status="$(curl -ksS "${resolve[@]}" -c "${cookie}" -o "${output}" -w '%{http_code}' \
    -H 'Content-Type: application/json' --data-binary "${payload}" "${base_url}/auth/register")"
  [[ "${status}" == "201" ]] || { echo "ERRO: registro retornou HTTP ${status}" >&2; cat "${output}" >&2; exit 1; }
  jq -e '.accountNumber | test("^[0-9]{8}$")' "${output}" >/dev/null
}

register 'José Gonçalves' "${work_dir}/source.cookies" "${work_dir}/source.json"
register 'Maria da Conceição' "${work_dir}/destination.cookies" "${work_dir}/destination.json"
jq -e '.ownerName | startswith("José Gonçalves")' "${work_dir}/source.json" >/dev/null
jq -e '.ownerName | startswith("Maria da Conceição")' "${work_dir}/destination.json" >/dev/null
source_id="$(jq -r .id "${work_dir}/source.json")"
destination_id="$(jq -r .id "${work_dir}/destination.json")"

kubectl -n databases exec statefulset/postgres -- psql -U appuser -d appdb -v ON_ERROR_STOP=1 -c \
  "BEGIN;
   UPDATE account_service.accounts SET balance=100.00 WHERE id='${source_id}'::uuid;
   INSERT INTO account_service.ledger_entries(journal_id,account_id,signed_amount,entry_type)
   VALUES ('${funding_journal_id}'::uuid,'${source_id}'::uuid,100.00,'E2E_FUNDING'),
          ('${funding_journal_id}'::uuid,NULL,-100.00,'SYSTEM_OFFSET');
   COMMIT;" >/dev/null

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
