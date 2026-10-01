# Upgrade para 0.5

`0.5.0-rc.2` preserva `bin/eks-assessment.sh` e o alias recomendado
`bin/kubernetes-assessment`. Scripts existentes continuam válidos.

Novas coletas incluem `collector-state.json`, `contract-validation.json`,
`manifest-schema-validation.json`, Operational Timeline e, quando Prometheus é
explícito, `node-process-evidence.json`. Coletas 0.4 continuam navegáveis e podem
ser validadas pelos contratos da sua própria versão; campos 0.5 ausentes aparecem
como `NOT_PRESENT`, sem serem inventados retroativamente.

A RC.2 adiciona `configuration-references.json`, `traffic-paths.json`,
`state-data-readiness.json`, `migration-probes.json` e
`blue-green-readiness.json` às novas coletas schema 4.1. ConfigMap/Secret metadata
permanece opt-in. `migration-comparison.*` só aparece após executar o Migration
Gate. Coletas schema 4.0 não recebem esses requisitos retroativamente.

Mudanças operacionais:

- progresso passa a ser ponderado pelo registry;
- `collect --resume <id>` reutiliza collectors aprovados e repete preflight;
- `--retry-failed` é necessário para repetir collectors em `FAIL`;
- Release Gate passa a gerar quatro formatos;
- package inclui provenance externa e o workflow gera attestation;
- validação de manifests sem OpenAPI/server-side dry-run permanece `PARTIAL`.
- opções 11/12 e subcomandos `blue-green-gate`/`migration-gate` usam exit code fail-closed;
- Secrets continuam fora do perfil padrão; o RBAC opcional é separado e namespaced.

Antes de promover:

1. execute os testes e o preflight;
2. gere uma coleta fresh;
3. execute `validate`, Release Gate e Regression Gate;
4. faça round-trip de um bundle;
5. verifique o package com `verify-release`;
6. confirme a attestation no GitHub para builds publicados.
