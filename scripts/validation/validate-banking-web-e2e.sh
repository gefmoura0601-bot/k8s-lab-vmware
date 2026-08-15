#!/usr/bin/env bash
set -euo pipefail
INGRESS_URL="${INGRESS_URL:-https://192.168.109.151:31882}"
HOST_HEADER="${HOST_HEADER:-nginx.lab.local}"
authority="${INGRESS_URL#https://}"; ip="${authority%%:*}"; port="${authority##*:}"
resolve=(--resolve "${HOST_HEADER}:${port}:${ip}")
kubectl -n banking rollout status deployment/banking-web deployment/account-service deployment/transaction-service --timeout=240s
html="$(curl -ksS "${resolve[@]}" "https://${HOST_HEADER}:${port}/banking/")"
grep -q '<title>Moura Banking</title>' <<<"${html}"
status="$(curl -ksS -o /dev/null -w '%{http_code}' "${resolve[@]}" "https://${HOST_HEADER}:${port}/bank/accounts/me")"
[[ "${status}" == "401" ]]
stamp="$(date +%s)"
register(){ curl -ksS "${resolve[@]}" -c "$2" -H 'Content-Type: application/json' -d "{\"ownerName\":\"$1 ${stamp}\",\"password\":\"MouraLab2026!\"}" "https://${HOST_HEADER}:${port}/bank/auth/register" >"$3"; jq -e '.accountNumber|test("^[0-9]{8}$")' "$3" >/dev/null; }
register "José Gonçalves" /tmp/moura-a.cookies /tmp/moura-a.json
register "Maria da Conceição" /tmp/moura-b.cookies /tmp/moura-b.json
jq -e '.ownerName | startswith("José Gonçalves")' /tmp/moura-a.json >/dev/null
jq -e '.ownerName | startswith("Maria da Conceição")' /tmp/moura-b.json >/dev/null
source_id="$(jq -r .id /tmp/moura-a.json)"; destination_id="$(jq -r .id /tmp/moura-b.json)"
journal_id="$(uuidgen)"
kubectl -n databases exec statefulset/postgres -- psql -U appuser -d appdb -v ON_ERROR_STOP=1 -c "BEGIN; UPDATE account_service.accounts SET balance=100.00 WHERE id='${source_id}'::uuid; INSERT INTO account_service.ledger_entries(journal_id,account_id,signed_amount,entry_type) VALUES ('${journal_id}'::uuid,'${source_id}'::uuid,100.00,'E2E_FUNDING'),('${journal_id}'::uuid,NULL,-100.00,'SYSTEM_OFFSET'); COMMIT;" >/dev/null
bad_status="$(curl -ksS "${resolve[@]}" -b /tmp/moura-a.cookies -o /dev/null -w '%{http_code}' -H 'Content-Type: application/json' -H "Idempotency-Key: $(uuidgen)" -d "{\"sourceAccountId\":\"${source_id}\",\"destinationAccountId\":\"${destination_id}\",\"amount\":1.25,\"description\":\"Senha incorreta\",\"password\":\"SenhaErrada2026!\"}" "https://${HOST_HEADER}:${port}/bank/transactions")"
[[ "${bad_status}" == "401" ]]
status="$(curl -ksS "${resolve[@]}" -b /tmp/moura-a.cookies -o /tmp/moura-transaction.json -w '%{http_code}' -H 'Content-Type: application/json' -H "Idempotency-Key: $(uuidgen)" -d "{\"sourceAccountId\":\"${source_id}\",\"destinationAccountId\":\"${destination_id}\",\"amount\":1.25,\"description\":\"E2E Moura Banking\",\"password\":\"MouraLab2026!\"}" "https://${HOST_HEADER}:${port}/bank/transactions")"
[[ "${status}" == "200" ]]; jq -e '.status=="COMPLETED"' /tmp/moura-transaction.json >/dev/null
curl -ksS "${resolve[@]}" -b /tmp/moura-a.cookies "https://${HOST_HEADER}:${port}/bank/transactions?sourceAccountId=${source_id}" >/tmp/moura-statement.json
jq -e 'length >= 1 and .[0].description == "E2E Moura Banking"' /tmp/moura-statement.json >/dev/null
curl -ksS "${resolve[@]}" -b /tmp/moura-b.cookies "https://${HOST_HEADER}:${port}/bank/transactions?sourceAccountId=${destination_id}" >/tmp/moura-incoming.json
jq -e 'length >= 1 and .[0].destinationAccountId == "'"${destination_id}"'"' /tmp/moura-incoming.json >/dev/null
echo "Moura Banking: autenticacao, isolamento, UTF-8 e transferencia validados."
