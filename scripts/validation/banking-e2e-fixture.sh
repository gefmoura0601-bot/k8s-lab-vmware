#!/usr/bin/env bash
set -euo pipefail

FIXTURE_NAMESPACE="${BANKING_E2E_FIXTURE_NAMESPACE:-banking}"
FIXTURE_DEPLOYMENT="${BANKING_E2E_FIXTURE_DEPLOYMENT:-banking-e2e-client}"
FIXTURE_CONTAINER="${BANKING_E2E_FIXTURE_CONTAINER:-psql-client}"

usage() {
  cat >&2 <<'EOF'
Uso:
  banking-e2e-fixture.sh wait
  banking-e2e-fixture.sh verify
  banking-e2e-fixture.sh fund <run-id> <account-id> <amount>
  banking-e2e-fixture.sh track <run-id> <payment-id>
  banking-e2e-fixture.sh cleanup <run-id>
  banking-e2e-fixture.sh cpf <run-id> <slot>
EOF
  exit 2
}

require_cmd() {
  command -v "$1" >/dev/null || {
    echo "ERRO: comando obrigatório ausente: $1" >&2
    exit 1
  }
}

require_uuid() {
  local value="$1" label="$2"
  [[ "${value}" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89aAbB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$ ]] || {
    echo "ERRO: ${label} não é um UUID válido" >&2
    exit 2
  }
}

require_amount() {
  local value="$1"
  [[ "${value}" =~ ^([0-9]{1,4})(\.[0-9]{1,2})?$ ]] || {
    echo 'ERRO: valor deve ser decimal positivo com no máximo duas casas' >&2
    exit 2
  }
  awk -v amount="${value}" 'BEGIN { exit !(amount >= 0.01 && amount <= 1000.00) }' || {
    echo 'ERRO: valor deve estar entre 0.01 e 1000.00' >&2
    exit 2
  }
}

fixture_cpf() {
  local run_id="$1" slot="$2" compact seed index chunk base sum digit remainder check_one check_two
  require_uuid "${run_id}" run-id
  [[ "${slot}" =~ ^[0-9]{1,3}$ ]] || {
    echo 'ERRO: slot do CPF deve ser um inteiro entre 0 e 999' >&2
    exit 2
  }

  compact="${run_id//-/}"
  seed=$((10#${slot} + 1))
  for ((index = 0; index < 32; index += 4)); do
    chunk="${compact:index:4}"
    seed=$(((seed * 65599 + 16#${chunk}) % 1000000000))
  done
  printf -v base '%09d' "${seed}"
  case "${base}" in
    000000000|111111111|222222222|333333333|444444444|555555555|666666666|777777777|888888888|999999999)
      seed=$(((seed + 123456789) % 1000000000))
      printf -v base '%09d' "${seed}"
      ;;
  esac

  sum=0
  for ((index = 0; index < 9; index++)); do
    digit="${base:index:1}"
    sum=$((sum + 10#${digit} * (10 - index)))
  done
  remainder=$((sum % 11))
  check_one=$((remainder < 2 ? 0 : 11 - remainder))

  sum=0
  for ((index = 0; index < 9; index++)); do
    digit="${base:index:1}"
    sum=$((sum + 10#${digit} * (11 - index)))
  done
  sum=$((sum + check_one * 2))
  remainder=$((sum % 11))
  check_two=$((remainder < 2 ? 0 : 11 - remainder))
  printf '%s%d%d\n' "${base}" "${check_one}" "${check_two}"
}

fixture_psql() {
  local sql="$1"
  shift
  printf '%s\n' "${sql}" | kubectl -n "${FIXTURE_NAMESPACE}" exec -i "deployment/${FIXTURE_DEPLOYMENT}" \
    -c "${FIXTURE_CONTAINER}" -- \
    psql -X -qAt -v ON_ERROR_STOP=1 -f - "$@"
}

fixture_verify() {
  [[ "$(fixture_psql 'SELECT banking_e2e.verify_access()')" == "t" ]] || {
    echo 'ERRO: a role da fixture possui privilégios inesperados ou funções indisponíveis' >&2
    return 1
  }
}

fixture_wait() {
  local attempt
  kubectl -n "${FIXTURE_NAMESPACE}" rollout status \
    "deployment/${FIXTURE_DEPLOYMENT}" --timeout=180s >/dev/null
  for attempt in {1..30}; do
    if fixture_verify >/dev/null 2>&1; then
      echo 'Fixture E2E disponível com privilégios mínimos.'
      return 0
    fi
    [[ "${attempt}" -lt 30 ]] || break
    sleep 2
  done
  echo 'ERRO: fixture E2E não ficou disponível' >&2
  return 1
}

fixture_fund() {
  local run_id="$1" account_id="$2" amount="$3"
  require_uuid "${run_id}" run-id
  require_uuid "${account_id}" account-id
  require_amount "${amount}"
  fixture_psql \
    "SELECT banking_e2e.fund_account(:'run_id'::uuid, :'account_id'::uuid, :'amount'::numeric)" \
    -v "run_id=${run_id}" \
    -v "account_id=${account_id}" \
    -v "amount=${amount}"
}

fixture_track() {
  local run_id="$1" payment_id="$2"
  require_uuid "${run_id}" run-id
  require_uuid "${payment_id}" payment-id
  fixture_psql \
    "SELECT banking_e2e.track_payment(:'run_id'::uuid, :'payment_id'::uuid)" \
    -v "run_id=${run_id}" \
    -v "payment_id=${payment_id}"
}

fixture_cleanup() {
  local run_id="$1"
  require_uuid "${run_id}" run-id
  fixture_psql \
    "SELECT banking_e2e.cleanup_run(:'run_id'::uuid)" \
    -v "run_id=${run_id}"
}

main() {
  case "${1:-}" in
    cpf)
      [[ "$#" -eq 3 ]] || usage
      fixture_cpf "$2" "$3"
      ;;
    wait)
      [[ "$#" -eq 1 ]] || usage
      require_cmd kubectl
      fixture_wait
      ;;
    verify)
      [[ "$#" -eq 1 ]] || usage
      require_cmd kubectl
      fixture_verify
      echo 'Fixture E2E validada.'
      ;;
    fund)
      [[ "$#" -eq 4 ]] || usage
      require_cmd kubectl
      require_cmd awk
      fixture_fund "$2" "$3" "$4"
      ;;
    track)
      [[ "$#" -eq 3 ]] || usage
      require_cmd kubectl
      fixture_track "$2" "$3"
      ;;
    cleanup)
      [[ "$#" -eq 2 ]] || usage
      require_cmd kubectl
      fixture_cleanup "$2"
      ;;
    *) usage ;;
  esac
}

main "$@"
