SHELL := /bin/bash

# ============================================================
# Variáveis padrão do lab
# Podem ser sobrescritas na linha de comando:
# make validate-postgres-api-mesh INGRESS_URL=... HOST_HEADER=...
# ============================================================
APP_NAME ?= postgres-api
APP_NAMESPACE ?= apps
APP_SELECTOR ?= app=postgres-api

SERVICE_PORT ?= 80
INTERNAL_HEALTH_PATH ?= /healthz
EXTERNAL_BASE_PATH ?= /postgres-api

INGRESS_URL ?= https://192.168.109.151:31882
HOST_HEADER ?= nginx.lab.local

GATEWAY_NAME ?= nginx-lab-gateway
GATEWAY_NAMESPACE ?= nginx-lab
VIRTUALSERVICE_NAME ?= postgres-api
DESTINATIONRULE_NAME ?= postgres-api
AUTHZPOLICY_NAME ?= postgres-api

VALIDATION_NAMESPACE ?= no-mesh-test
NEGATIVE_POD_NAME ?= curl-negative
NEGATIVE_IMAGE ?= curlimages/curl:8.10.1

VALIDATION_SCRIPT ?= ./scripts/validation/validate-postgres-api-mesh.sh
POSTGRES_API_KUSTOMIZE_PATH ?= kubernetes/apps/postgres-api
RENDERED_POSTGRES_API ?= /tmp/postgres-api-rendered.yaml

.PHONY: \
	help \
	print-postgres-api-mesh-vars \
	validate-postgres-api-manifests \
	validate-postgres-api-mesh \
	validate-all

help:
	@echo ""
	@echo "Targets disponíveis:"
	@echo "  make print-postgres-api-mesh-vars"
	@echo "  make validate-postgres-api-manifests"
	@echo "  make validate-postgres-api-mesh"
	@echo "  make validate-all"
	@echo ""
	@echo "Exemplos:"
	@echo "  make validate-postgres-api-manifests"
	@echo "  make validate-postgres-api-mesh"
	@echo "  make validate-postgres-api-mesh INGRESS_URL=https://192.168.109.151:31882 HOST_HEADER=nginx.lab.local"
	@echo ""

print-postgres-api-mesh-vars:
	@echo ""
	@echo "APP_NAME=$(APP_NAME)"
	@echo "APP_NAMESPACE=$(APP_NAMESPACE)"
	@echo "APP_SELECTOR=$(APP_SELECTOR)"
	@echo "SERVICE_PORT=$(SERVICE_PORT)"
	@echo "INTERNAL_HEALTH_PATH=$(INTERNAL_HEALTH_PATH)"
	@echo "EXTERNAL_BASE_PATH=$(EXTERNAL_BASE_PATH)"
	@echo "INGRESS_URL=$(INGRESS_URL)"
	@echo "HOST_HEADER=$(HOST_HEADER)"
	@echo "GATEWAY_NAME=$(GATEWAY_NAME)"
	@echo "GATEWAY_NAMESPACE=$(GATEWAY_NAMESPACE)"
	@echo "VIRTUALSERVICE_NAME=$(VIRTUALSERVICE_NAME)"
	@echo "DESTINATIONRULE_NAME=$(DESTINATIONRULE_NAME)"
	@echo "AUTHZPOLICY_NAME=$(AUTHZPOLICY_NAME)"
	@echo "VALIDATION_NAMESPACE=$(VALIDATION_NAMESPACE)"
	@echo "NEGATIVE_POD_NAME=$(NEGATIVE_POD_NAME)"
	@echo "NEGATIVE_IMAGE=$(NEGATIVE_IMAGE)"
	@echo "VALIDATION_SCRIPT=$(VALIDATION_SCRIPT)"
	@echo "POSTGRES_API_KUSTOMIZE_PATH=$(POSTGRES_API_KUSTOMIZE_PATH)"
	@echo "RENDERED_POSTGRES_API=$(RENDERED_POSTGRES_API)"
	@echo ""

validate-postgres-api-manifests:
	@echo ""
	@echo "Renderizando manifests da postgres-api..."
	@kubectl kustomize "$(POSTGRES_API_KUSTOMIZE_PATH)" > "$(RENDERED_POSTGRES_API)"
	@test -s "$(RENDERED_POSTGRES_API)" || { echo "Render falhou: arquivo vazio em $(RENDERED_POSTGRES_API)"; exit 1; }
	@echo "Primeiras linhas do render:"
	@head -n 120 "$(RENDERED_POSTGRES_API)"
	@echo ""
	@echo "Kinds renderizados:"
	@grep '^kind:' "$(RENDERED_POSTGRES_API)" | sort | uniq
	@echo ""
	@echo "Validando recursos esperados..."
	@grep -q '^kind: Namespace$$' "$(RENDERED_POSTGRES_API)"
	@grep -q '^kind: Service$$' "$(RENDERED_POSTGRES_API)"
	@grep -q '^kind: Deployment$$' "$(RENDERED_POSTGRES_API)"
	@grep -q '^kind: PodDisruptionBudget$$' "$(RENDERED_POSTGRES_API)"
	@grep -q '^kind: VirtualService$$' "$(RENDERED_POSTGRES_API)"
	@grep -q '^kind: DestinationRule$$' "$(RENDERED_POSTGRES_API)"
	@grep -q '^kind: AuthorizationPolicy$$' "$(RENDERED_POSTGRES_API)"
	@grep -q '^kind: ServiceMonitor$$' "$(RENDERED_POSTGRES_API)"
	@echo "Validando nomes esperados..."
	@grep -q 'name: postgres-api' "$(RENDERED_POSTGRES_API)"
	@grep -q 'namespace: apps' "$(RENDERED_POSTGRES_API)"
	@grep -q 'host: postgres-api.apps.svc.cluster.local' "$(RENDERED_POSTGRES_API)" || \
	grep -q 'host: postgres-api.apps.svc.cluster.local' "$(RENDERED_POSTGRES_API)"
	@echo "Validação estática dos manifests da postgres-api concluída com sucesso."

validate-postgres-api-mesh:
	@test -x "$(VALIDATION_SCRIPT)" || { echo "Script não encontrado ou sem permissão de execução: $(VALIDATION_SCRIPT)"; exit 1; }
	@echo ""
	@echo "Executando validação do mesh da postgres-api..."
	@APP_NAME="$(APP_NAME)" \
	APP_NAMESPACE="$(APP_NAMESPACE)" \
	APP_SELECTOR="$(APP_SELECTOR)" \
	SERVICE_PORT="$(SERVICE_PORT)" \
	INTERNAL_HEALTH_PATH="$(INTERNAL_HEALTH_PATH)" \
	EXTERNAL_BASE_PATH="$(EXTERNAL_BASE_PATH)" \
	INGRESS_URL="$(INGRESS_URL)" \
	HOST_HEADER="$(HOST_HEADER)" \
	GATEWAY_NAME="$(GATEWAY_NAME)" \
	GATEWAY_NAMESPACE="$(GATEWAY_NAMESPACE)" \
	VIRTUALSERVICE_NAME="$(VIRTUALSERVICE_NAME)" \
	DESTINATIONRULE_NAME="$(DESTINATIONRULE_NAME)" \
	AUTHZPOLICY_NAME="$(AUTHZPOLICY_NAME)" \
	VALIDATION_NAMESPACE="$(VALIDATION_NAMESPACE)" \
	NEGATIVE_POD_NAME="$(NEGATIVE_POD_NAME)" \
	NEGATIVE_IMAGE="$(NEGATIVE_IMAGE)" \
	"$(VALIDATION_SCRIPT)"

validate-all: validate-postgres-api-manifests validate-postgres-api-mesh
	@echo ""
	@echo "Validação completa concluída com sucesso."
