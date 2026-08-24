#!/usr/bin/env bash
set -euo pipefail

INGRESS_URL="${INGRESS_URL:-https://192.168.109.151:31882}"
HOST_HEADER="${HOST_HEADER:-nginx.lab.local}"
PASSWORD="${BANKING_E2E_PASSWORD:-MouraCardsLab2026!}"
EVIDENCE_FILE="${BANKING_CARDS_EVIDENCE_FILE:-}"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
fixture_script="${script_dir}/banking-e2e-fixture.sh"

authority="${INGRESS_URL#https://}"
ip="${authority%%:*}"
port="${authority##*:}"
bank_url="https://${HOST_HEADER}:${port}/bank"
store_url="https://${HOST_HEADER}:${port}/store"
resolve=(--resolve "${HOST_HEADER}:${port}:${ip}")
work_dir="$(mktemp -d)"
cookie_file="${work_dir}/account.cookies"
HTTP_STATUS=""
HTTP_BODY=""
CHECKOUT_PAYLOAD=""

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

cleanup() {
  local status=$? cleanup_failed=0
  trap - EXIT
  set +e
  if ! bash "${fixture_script}" cleanup "${run_id}" >/dev/null 2>&1; then
    cleanup_failed=1
    echo 'ERRO: a fixture não conseguiu remover os dados temporários do teste de cartões' >&2
  fi
  unset HTTP_BODY CHECKOUT_PAYLOAD cpf debit_pan debit_cvv credit_pan credit_cvv bad_cvv
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

require_cmd() {
  command -v "$1" >/dev/null || {
    echo "ERRO: comando obrigatório ausente: $1" >&2
    exit 1
  }
}

fail() {
  echo "ERRO: $1" >&2
  exit 1
}

request_json() {
  local method="$1" url="$2" payload="$3" response
  shift 3
  response="$(printf '%s' "${payload}" | curl -ksS "${resolve[@]}" \
    -X "${method}" -H 'Content-Type: application/json' -H 'Accept: application/json' \
    "$@" --data-binary @- "${url}" -w $'\n%{http_code}')"
  HTTP_STATUS="${response##*$'\n'}"
  HTTP_BODY="${response%$'\n'*}"
}

request_get() {
  local url="$1" response
  shift
  response="$(curl -ksS "${resolve[@]}" -H 'Accept: application/json' "$@" "${url}" -w $'\n%{http_code}')"
  HTTP_STATUS="${response##*$'\n'}"
  HTTP_BODY="${response%$'\n'*}"
}

expect_status() {
  local expected="$1" operation="$2"
  [[ "${HTTP_STATUS}" == "${expected}" ]] || fail "${operation} retornou HTTP ${HTTP_STATUS}; esperado ${expected}"
}

checkout_payload() {
  local product_id="$1" quantity="$2" pan="$3" holder="$4" expiry_month="$5"
  local expiry_year="$6" cvv="$7" payment_type="$8" installments="$9"
  printf -v CHECKOUT_PAYLOAD \
    '{"productId":"%s","quantity":%d,"card":{"number":"%s","holderName":"%s","expiryMonth":%d,"expiryYear":%d,"cvv":"%s"},"paymentType":"%s","installments":%d}' \
    "${product_id}" "${quantity}" "${pan}" "${holder}" "${expiry_month}" \
    "${expiry_year}" "${cvv}" "${payment_type}" "${installments}"
}

for command in kubectl curl jq awk; do require_cmd "${command}"; done
[[ -r "${fixture_script}" ]] || fail "fixture ausente: ${fixture_script}"
umask 077

kubectl -n banking rollout status \
  deployment/account-service deployment/acquirer-service deployment/store-service --timeout=300s
bash "${fixture_script}" wait

request_get "${store_url}/api/catalog"
expect_status 200 'catálogo da loja'
printf '%s' "${HTTP_BODY}" | jq -e \
  '.currency=="BRL" and any(.products[]; .id=="mug" and .price==39.90) and any(.products[]; .id=="keyboard" and .price==249.90)' >/dev/null \
  || fail 'catálogo da loja não contém os produtos esperados'

cpf="$(bash "${fixture_script}" cpf "${run_id}" 5)"
owner="E2E:${run_id}:cards-holder"
register_payload="$(jq -nc --arg owner "${owner}" --arg cpf "${cpf}" --arg password "${PASSWORD}" \
  '{ownerName:$owner,cpf:$cpf,password:$password}')"
request_json POST "${bank_url}/auth/register" "${register_payload}" -c "${cookie_file}"
expect_status 201 'cadastro da conta de cartões'
account_id="$(printf '%s' "${HTTP_BODY}" | jq -er '.id')"
printf '%s' "${HTTP_BODY}" | jq -e --arg owner "${owner}" \
  '.ownerName==$owner and (.accountNumber|test("^[0-9]{8}$")) and (.cpfMasked|type=="string")' >/dev/null \
  || fail 'resposta de cadastro inválida'
unset register_payload HTTP_BODY

duplicate_payload="$(jq -nc --arg owner "E2E:${run_id}:duplicate-cpf" --arg cpf "${cpf}" --arg password "${PASSWORD}" \
  '{ownerName:$owner,cpf:$cpf,password:$password}')"
request_json POST "${bank_url}/auth/register" "${duplicate_payload}"
expect_status 409 'cadastro com CPF duplicado'
unset duplicate_payload cpf HTTP_BODY

bash "${fixture_script}" fund "${run_id}" "${account_id}" 100.00 >/dev/null

issue_payload="$(jq -nc --arg password "${PASSWORD}" '{type:"DEBIT",password:$password}')"
request_json POST "${bank_url}/accounts/me/cards" "${issue_payload}" -b "${cookie_file}"
expect_status 201 'emissão do cartão de débito virtual'
printf '%s' "${HTTP_BODY}" | jq -e \
  '.type=="DEBIT" and .formFactor=="VIRTUAL" and .status=="ACTIVE" and (.number|test("^[0-9]{13,19}$")) and (.cvv|test("^[0-9]{3}$"))' >/dev/null \
  || fail 'cartão de débito retornou detalhes inválidos'
debit_card_id="$(printf '%s' "${HTTP_BODY}" | jq -er '.id')"
debit_pan="$(printf '%s' "${HTTP_BODY}" | jq -er '.number')"
debit_cvv="$(printf '%s' "${HTTP_BODY}" | jq -er '.cvv')"
debit_holder="$(printf '%s' "${HTTP_BODY}" | jq -er '.holderName')"
debit_month="$(printf '%s' "${HTTP_BODY}" | jq -er '.expiryMonth')"
debit_year="$(printf '%s' "${HTTP_BODY}" | jq -er '.expiryYear')"
debit_last4="${debit_pan: -4}"
unset HTTP_BODY

issue_payload="$(jq -nc --arg password "${PASSWORD}" '{type:"CREDIT",password:$password}')"
request_json POST "${bank_url}/accounts/me/cards" "${issue_payload}" -b "${cookie_file}"
expect_status 201 'emissão do cartão de crédito virtual'
printf '%s' "${HTTP_BODY}" | jq -e \
  '.type=="CREDIT" and .formFactor=="VIRTUAL" and .status=="ACTIVE" and .creditLimit==1000.00 and .usedAmount==0.00 and .availableAmount==1000.00 and (.number|test("^[0-9]{13,19}$")) and (.cvv|test("^[0-9]{3}$"))' >/dev/null \
  || fail 'cartão de crédito retornou detalhes inválidos'
credit_card_id="$(printf '%s' "${HTTP_BODY}" | jq -er '.id')"
credit_pan="$(printf '%s' "${HTTP_BODY}" | jq -er '.number')"
credit_cvv="$(printf '%s' "${HTTP_BODY}" | jq -er '.cvv')"
credit_holder="$(printf '%s' "${HTTP_BODY}" | jq -er '.holderName')"
credit_month="$(printf '%s' "${HTTP_BODY}" | jq -er '.expiryMonth')"
credit_year="$(printf '%s' "${HTTP_BODY}" | jq -er '.expiryYear')"
credit_last4="${credit_pan: -4}"
unset HTTP_BODY issue_payload

[[ "${debit_holder}" == "${owner}" && "${credit_holder}" == "${owner}" ]] \
  || fail 'titular dos cartões não corresponde à conta E2E'

debit_payment_id="$(new_uuid)"
bash "${fixture_script}" track "${run_id}" "${debit_payment_id}" >/dev/null
checkout_payload mug 1 "${debit_pan}" "${debit_holder}" "${debit_month}" "${debit_year}" "${debit_cvv}" DEBIT 1
request_json POST "${store_url}/api/checkout" "${CHECKOUT_PAYLOAD}" -H "Idempotency-Key: ${debit_payment_id}"
expect_status 200 'compra no débito'
printf '%s' "${HTTP_BODY}" | jq -e --arg id "${debit_payment_id}" --arg last4 "${debit_last4}" \
  '.paymentId==$id and .orderId==$id and .status=="CAPTURED" and .cardType=="DEBIT" and .amount==39.90 and .last4==$last4' >/dev/null \
  || fail 'compra no débito não foi capturada corretamente'
debit_authorization="$(printf '%s' "${HTTP_BODY}" | jq -er '.authorizationCode')"
unset HTTP_BODY CHECKOUT_PAYLOAD

checkout_payload mug 1 "${debit_pan}" "${debit_holder}" "${debit_month}" "${debit_year}" "${debit_cvv}" DEBIT 1
request_json POST "${store_url}/api/checkout" "${CHECKOUT_PAYLOAD}" -H "Idempotency-Key: ${debit_payment_id}"
expect_status 200 'retry idempotente no débito'
printf '%s' "${HTTP_BODY}" | jq -e --arg id "${debit_payment_id}" --arg authorization "${debit_authorization}" \
  '.paymentId==$id and .status=="CAPTURED" and .authorizationCode==$authorization' >/dev/null \
  || fail 'retry idempotente retornou uma decisão diferente'
unset HTTP_BODY CHECKOUT_PAYLOAD

checkout_payload mug 2 "${debit_pan}" "${debit_holder}" "${debit_month}" "${debit_year}" "${debit_cvv}" DEBIT 1
request_json POST "${store_url}/api/checkout" "${CHECKOUT_PAYLOAD}" -H "Idempotency-Key: ${debit_payment_id}"
expect_status 409 'conflito de idempotência'
printf '%s' "${HTTP_BODY}" | jq -e '.code|ascii_downcase=="idempotency_conflict"' >/dev/null \
  || fail 'conflito de idempotência retornou código inesperado'
unset HTTP_BODY CHECKOUT_PAYLOAD

credit_payment_id="$(new_uuid)"
bash "${fixture_script}" track "${run_id}" "${credit_payment_id}" >/dev/null
checkout_payload keyboard 1 "${credit_pan}" "${credit_holder}" "${credit_month}" "${credit_year}" "${credit_cvv}" CREDIT 3
request_json POST "${store_url}/api/checkout" "${CHECKOUT_PAYLOAD}" -H "Idempotency-Key: ${credit_payment_id}"
expect_status 200 'compra no crédito'
printf '%s' "${HTTP_BODY}" | jq -e --arg id "${credit_payment_id}" --arg last4 "${credit_last4}" \
  '.paymentId==$id and .status=="CAPTURED" and .cardType=="CREDIT" and .amount==249.90 and .last4==$last4' >/dev/null \
  || fail 'compra no crédito não foi capturada corretamente'
unset HTTP_BODY CHECKOUT_PAYLOAD

bad_cvv=000
[[ "${credit_cvv}" == 000 ]] && bad_cvv=001
invalid_cvv_payment_id="$(new_uuid)"
bash "${fixture_script}" track "${run_id}" "${invalid_cvv_payment_id}" >/dev/null
checkout_payload mug 1 "${credit_pan}" "${credit_holder}" "${credit_month}" "${credit_year}" "${bad_cvv}" CREDIT 1
request_json POST "${store_url}/api/checkout" "${CHECKOUT_PAYLOAD}" -H "Idempotency-Key: ${invalid_cvv_payment_id}"
expect_status 200 'recusa por CVV inválido'
printf '%s' "${HTTP_BODY}" | jq -e \
  '.status=="DECLINED" and .declineCode=="INVALID_SECURITY_CODE" and .cardType=="CREDIT"' >/dev/null \
  || fail 'CVV inválido não foi recusado pelo emissor'
unset HTTP_BODY CHECKOUT_PAYLOAD bad_cvv

insufficient_payment_id="$(new_uuid)"
bash "${fixture_script}" track "${run_id}" "${insufficient_payment_id}" >/dev/null
checkout_payload headphones 1 "${debit_pan}" "${debit_holder}" "${debit_month}" "${debit_year}" "${debit_cvv}" DEBIT 1
request_json POST "${store_url}/api/checkout" "${CHECKOUT_PAYLOAD}" -H "Idempotency-Key: ${insufficient_payment_id}"
expect_status 200 'recusa por saldo insuficiente'
printf '%s' "${HTTP_BODY}" | jq -e \
  '.status=="DECLINED" and .declineCode=="INSUFFICIENT_FUNDS" and .cardType=="DEBIT"' >/dev/null \
  || fail 'compra acima do saldo não foi recusada'
unset HTTP_BODY CHECKOUT_PAYLOAD

request_get "${bank_url}/accounts/me" -b "${cookie_file}"
expect_status 200 'consulta de saldo após compras'
printf '%s' "${HTTP_BODY}" | jq -e '.balance==60.10' >/dev/null \
  || fail 'saldo após compra no débito está incorreto'

request_get "${bank_url}/accounts/me/cards" -b "${cookie_file}"
expect_status 200 'consulta dos cartões'
printf '%s' "${HTTP_BODY}" | jq -e \
  --arg debit "${debit_card_id}" --arg credit "${credit_card_id}" \
  'length==2
   and any(.[]; .id==$debit and .type=="DEBIT" and .formFactor=="VIRTUAL" and .availableAmount==60.10)
   and any(.[]; .id==$credit and .type=="CREDIT" and .formFactor=="VIRTUAL"
                    and .creditLimit==1000.00 and .usedAmount==249.90 and .availableAmount==750.10)' >/dev/null \
  || fail 'saldo de débito ou utilização do limite de crédito está incorreto'

request_get "${bank_url}/accounts/me/card-purchases" -b "${cookie_file}"
expect_status 200 'histórico de compras com cartão'
printf '%s' "${HTTP_BODY}" | jq -e \
  --arg debit "${debit_payment_id}" --arg credit "${credit_payment_id}" \
  --arg invalid "${invalid_cvv_payment_id}" --arg insufficient "${insufficient_payment_id}" \
  'length==4
   and any(.[]; .paymentId==$debit and .status=="CAPTURED" and .paymentType=="DEBIT")
   and any(.[]; .paymentId==$credit and .status=="CAPTURED" and .paymentType=="CREDIT")
   and any(.[]; .paymentId==$invalid and .status=="DECLINED" and .declineCode=="INVALID_SECURITY_CODE")
   and any(.[]; .paymentId==$insufficient and .status=="DECLINED" and .declineCode=="INSUFFICIENT_FUNDS")' >/dev/null \
  || fail 'histórico não representa exatamente as quatro decisões esperadas'

unset HTTP_BODY debit_pan debit_cvv credit_pan credit_cvv debit_authorization

if [[ -n "${EVIDENCE_FILE}" ]]; then
  jq -n --arg runId "${run_id}" \
    '{runId:$runId,cards:{debit:"VIRTUAL",credit:"VIRTUAL"},captured:2,declined:2,idempotentRetry:true,idempotencyConflict:true,cpfDuplicateRejected:true,sensitiveCardDataIncluded:false}' \
    >"${EVIDENCE_FILE}"
fi

echo 'Moura Banking Cards: débito, crédito, loja, adquirência, idempotência, recusas, saldos e limpeza validados.'
