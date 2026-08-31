# Upgrade para 0.4

Não há migração destrutiva. Coleções `0.3` continuam visíveis; as novas abas informarão ausência de `operational-insights.json` até uma nova coleta.

Coleções anteriores à `0.4.0-rc.3` não possuem `cloud-provider-assessment.json`. Elas continuam navegáveis, mas Cloud Provider e lifecycle regional aparecem como `N/A`/`UNKNOWN` até uma nova coleta.

Logs continuam desabilitados por padrão. Não habilite `ASSESSMENT_INCLUDE_LOGS=1` sem revisar targets, retenção e política de dados do ambiente.

Após extrair o pacote, execute:

```bash
bin/eks-assessment.sh --version
bash src/assessment-preflight.sh
```

O pacote agora inclui `data/lifecycle-catalog.json`. Não remova esse diretório. O catálogo informa sua data `asOf`; quando ultrapassa o limite de staleness, versões ainda em suporte são exibidas como `UNKNOWN_STALE_CATALOG` até a publicação de um catálogo revisado.

Para AKS ou GKE, configure os escopos opcionais descritos em `docs/cloud-provider-evidence.md`. A ausência deles não impede o assessment Kubernetes, apenas mantém a cobertura cloud incompleta.

O validator da RC.3 também rejeita findings duplicados, conflitos de severidade e `PASS` com confiança baixa. Coleções interrompidas preservam `CANCELLED` ou `TIMED_OUT` desde o preflight, e as métricas de duração, API, memória e tamanho ficam disponíveis em Coverage para calibração operacional.
