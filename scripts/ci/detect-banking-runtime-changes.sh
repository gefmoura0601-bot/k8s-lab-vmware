#!/usr/bin/env bash
set -euo pipefail

event_name="${EVENT_NAME:-}"
before_sha="${BEFORE_SHA:-}"
current_sha="${CURRENT_SHA:-HEAD}"
output_file="${GITHUB_OUTPUT:-}"

account=false
transaction=false
acquirer=false
store=false

select_all() {
  account=true
  transaction=true
  acquirer=true
  store=true
}

select_path() {
  local path="$1"
  case "$path" in
    .github/workflows/banking-images-ci.yaml)
      select_all
      ;;
    app/account-service-java/.dockerignore|app/account-service-java/Dockerfile|app/account-service-java/pom.xml|app/account-service-java/src/main/*)
      account=true
      ;;
    app/transaction-service-dotnet/.dockerignore|app/transaction-service-dotnet/Dockerfile|app/transaction-service-dotnet/appsettings.json|app/transaction-service-dotnet/*.cs|app/transaction-service-dotnet/*.csproj)
      transaction=true
      ;;
    app/acquirer-service-dotnet/.dockerignore|app/acquirer-service-dotnet/Dockerfile|app/acquirer-service-dotnet/appsettings.json|app/acquirer-service-dotnet/*.cs|app/acquirer-service-dotnet/*.csproj)
      acquirer=true
      ;;
    app/store-service-go/.dockerignore|app/store-service-go/Dockerfile|app/store-service-go/go.mod|app/store-service-go/go.sum|app/store-service-go/main.go|app/store-service-go/static/*)
      store=true
      ;;
  esac
}

if [[ "$event_name" == "workflow_dispatch" || -z "$before_sha" || "$before_sha" =~ ^0+$ ]]; then
  select_all
else
  git cat-file -e "${before_sha}^{commit}" 2>/dev/null || {
    printf 'ERRO: commit anterior não disponível: %s\n' "$before_sha" >&2
    exit 1
  }
  git cat-file -e "${current_sha}^{commit}" 2>/dev/null || {
    printf 'ERRO: commit atual não disponível: %s\n' "$current_sha" >&2
    exit 1
  }
  while IFS= read -r path; do
    select_path "$path"
  done < <(git diff --name-only "$before_sha" "$current_sha")
fi

outputs=(
  "account=$account"
  "transaction=$transaction"
  "acquirer=$acquirer"
  "store=$store"
)

if [[ -n "$output_file" ]]; then
  printf '%s\n' "${outputs[@]}" >> "$output_file"
else
  printf '%s\n' "${outputs[@]}"
fi

printf 'Serviços selecionados: account=%s transaction=%s acquirer=%s store=%s\n' \
  "$account" "$transaction" "$acquirer" "$store" >&2
