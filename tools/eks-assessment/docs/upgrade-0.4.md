# Upgrade para 0.4

Não há migração destrutiva. Coleções `0.3` continuam visíveis; as novas abas informarão ausência de `operational-insights.json` até uma nova coleta.

Coleções anteriores à `0.4.0-rc.3` não possuem `cloud-provider-assessment.json`. Elas continuam navegáveis, mas Cloud Provider e lifecycle regional aparecem como `N/A`/`UNKNOWN` até uma nova coleta.

Logs continuam desabilitados por padrão. Não habilite `ASSESSMENT_INCLUDE_LOGS=1` sem revisar targets, retenção e política de dados do ambiente.

Na RC.6, use `src/provider_validation.py` somente após uma coleta `COMPLETED`. O argumento `--expected-provider` é obrigatório e `WARN` não promove a release. Consulte `docs/provider-validation.md` antes de definir os budgets do ambiente transacional.

Na RC.7, o mesmo gate está disponível na opção 7 do menu e em **Governança → Release Gate** no dashboard. A escolha do provider continua obrigatória e explícita. O dashboard usa os thresholds padrão; use o CLI para uma policy customizada.

Na RC.8, a opção 8 e **Governança → Regression Gate** comparam duas coletas offline. O profile `standard` bloqueia regressões novas; `strict` também bloqueia riscos e lacunas nos findings atuais. A policy está em `data/assessment-policy.json` e gera JSON, JUnit e SARIF na coleta atual. Coleções antigas sem os contratos obrigatórios permanecem visíveis, mas não são aprovadas sem evidência.

Na RC.9, `bin/eks-assessment.sh` também funciona como CLI headless. Pipelines devem informar `--phase`, `--change-id` e as opções de escopo explicitamente. Sem `--prometheus-url` ou `--auto-detect-prometheus`, Prometheus permanece desabilitado. Scripts existentes sem argumentos continuam abrindo o menu normalmente.

Na versão `0.4.0`, o profile `generic-kubernetes` passa a estável. Os profiles
EKS, AKS e GKE permanecem `PREVIEW`; não interprete a matriz offline como
qualificação de um cloud provider. Consulte `data/release-qualification.json`
antes de automatizar uma promoção.

Após extrair o pacote, execute:

```bash
bin/eks-assessment.sh --version
bash src/assessment-preflight.sh
```

O pacote agora inclui `data/lifecycle-catalog.json`. Não remova esse diretório. O catálogo informa sua data `asOf`; quando ultrapassa o limite de staleness, versões ainda em suporte são exibidas como `UNKNOWN_STALE_CATALOG` até a publicação de um catálogo revisado.

Para AKS ou GKE, configure os escopos opcionais descritos em `docs/cloud-provider-evidence.md`. A ausência deles não impede o assessment Kubernetes, apenas mantém a cobertura cloud incompleta.

O validator da RC.3 também rejeita findings duplicados, conflitos de severidade e `PASS` com confiança baixa. Coleções interrompidas preservam `CANCELLED` ou `TIMED_OUT` desde o preflight, e as métricas de duração, API, memória e tamanho ficam disponíveis em Coverage para calibração operacional.
