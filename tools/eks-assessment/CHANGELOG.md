# Changelog

## 0.4.0 — 2026-09-29

- promove o profile `generic-kubernetes` a estável após a matriz read-only,
  coletas on-premises, cancelamento, timeout, logs sanitizados e gates offline;
- mantém EKS, AKS e GKE explicitamente como `PREVIEW` até validação em ambientes
  reais, registrada em `data/release-qualification.json`;
- executa Release Gate e Regression Gate headless no CI com outputs JSON, JUnit
  e SARIF publicados como artifact;
- persiste `namespaceScope` na metadata e bloqueia comparações entre coleta
  cluster-wide e coleta namespaced sem gerar deltas enganosos;
- valida o pacote portátil, checksum, SBOM, sintaxe e versão antes da publicação;
- publica o SBOM SPDX como asset independente e usa checksum relativo, portátil
  após o download;
- preserva todos os contratos e formatos de artefato da série `0.4.0-rc`.

## 0.4.0-rc.9 — 2026-09-28

- adiciona CLI headless para `preflight`, `collect`, `list`, `compare`, `terminal`, `dashboard`, `release-gate` e `regression-gate`;
- exige argumentos explícitos e retorna exit codes adequados para automação e CI/CD;
- mantém o menu como comportamento padrão e preserva os mesmos contratos read-only, timeout e cancelamento;
- desabilita autodetecção de Prometheus no modo headless por padrão, salvo opt-in explícito;
- permite executar gates offline sem exigir `kubectl` ou conectividade com o cluster;
- documenta execução reproduzível, outputs machine-friendly e exemplos limitados a Kubernetes genérico.

## 0.4.0-rc.8 — 2026-09-28

- adiciona `Regression Gate` provider-neutral e offline entre duas coletas sanitizadas;
- publica `assessment-policy.json` versionado com profiles `standard` e `strict` e validação fail-closed do schema;
- compara findings por fingerprint estável, CIS, Node Health, Manifest Quality, lifecycle, quality gate e impacto medido;
- impede `PASS` quando identidade do cluster, estado terminal, integridade ou evidência obrigatória não são comprovados;
- integra o fluxo à opção 8 do menu e a **Governança → Regression Gate** no dashboard;
- grava JSON, JUnit e SARIF de forma atômica para consumo em CI/CD;
- adiciona regressões automatizadas para policy, gates, autenticação, persistência e exportações;
- mantém a qualificação real de EKS, AKS e GKE explicitamente adiada, sem inferir validação cloud.

## 0.4.0-rc.7 — 2026-09-28

- integra o `Provider Validation Runner` ao menu como opção 7 e ao dashboard em `Governança → Release Gate`;
- mantém a escolha do provider esperado explícita, sem usar a autodetecção como expectativa;
- executa o gate somente sobre artefatos sanitizados, sem novas chamadas ao Kubernetes ou ao Cloud Provider;
- grava `provider-validation.json` de forma atômica e serializa coleta/validação com o mesmo lock operacional;
- protege a execução web com autenticação, action token, allowlist de providers e validação estrita do ID da coleta;
- adiciona visualização auditável dos gates, fontes independentes, policy, thresholds e inventário, além de busca e exportação JSON;
- adiciona regressões para renderização, persistência atômica, token de ação, provider inválido e redirect de sucesso.

## 0.4.0-rc.6 — 2026-08-31

- adiciona `Provider Validation Runner` offline com provider esperado obrigatório;
- cruza detecção por Kubernetes evidence, Cloud Provider artifact e Operational Insights;
- valida estado terminal, integridade, read-only, zero mutações, proteção de dados, aplicabilidade, quality gate, cobertura e performance;
- aplica budgets configuráveis para duração, chamadas, retries, throttling, bytes e peak RSS;
- mantém `releaseReady=false` para qualquer `WARN` ou `FAIL` e não trata Cloud Provider API parcial como aprovação;
- adiciona matriz sanitizada de regressão para EKS, AKS, GKE e Kubernetes genérico;
- documenta o fluxo de validação real sem presumir acesso aos ambientes cloud.

## 0.4.0-rc.5 — 2026-08-31

- corrige a renderização do painel de indicadores em `Cloud Provider`, que exibia o placeholder literal `{facts}`;
- adiciona regressão unitária e smoke HTTP para impedir placeholders não resolvidos nessa página;
- impede o smoke HTTP de selecionar coletas canceladas ou sem os artefatos obrigatórios;
- registra a medição live de impacto da RC.5 no lab, sem extrapolá-la para ambientes transacionais;
- atualiza a evidência local do roadmap sem presumir validação em EKS, AKS ou GKE reais.

## 0.4.0-rc.4 — 2026-08-31

- torna o banner de saúde acionável, explicando por que o ambiente está `CRÍTICO` e destacando os principais fatores;
- adiciona `Node Health` provider-neutral para on-premises, EKS, AKS e GKE;
- coleta Node e Pod metrics via `metrics.k8s.io` com RBAC estritamente read-only;
- separa uso observado entre Kubernetes/System Pods, DaemonSets, application workloads, headroom e `Node overhead / não atribuído`;
- combina uso, requests, reserva, densidade de Pods, `Ready` e condições de pressão sem presumir acesso ao node;
- impede `PASS` de Node Health quando a Metrics API ou a condição `Ready` não fornecem evidência suficiente;
- troca lifecycle genérico de imagens detectadas de `UNKNOWN` para `EVIDENCE_UNAVAILABLE`, mantendo `UNKNOWN` para versão realmente indeterminada;
- documenta thresholds, fórmulas, limitações de atribuição e permissões mínimas.

## 0.4.0-rc.3 — 2026-08-30

- adiciona `cloud-provider-assessment.json` com evidência read-only normalizada para EKS, AKS e GKE;
- automatiza `az aks show/get-upgrades/nodepool list/get-versions` e `gcloud container clusters describe/get-server-config` quando escopo e identidade estão disponíveis;
- não persiste payloads cloud brutos, credenciais, endpoints ou identificadores de account/subscription/project;
- bloqueia comandos cloud fora da allowlist read-only antes de iniciar qualquer subprocesso;
- adiciona catálogo oficial versionado de lifecycle e impede status suportado quando o catálogo está vencido;
- converte Best Practices de provider em `PASS`/`WARN` somente quando há evidência da Cloud Provider API;
- adiciona página Cloud Provider, exportação sanitizada, busca global e navegação agrupada;
- publica exemplos mínimos read-only para Azure RBAC e GCP IAM;
- mantém compatibilidade com respostas atuais e legadas de versões do AKS sem inferir suporte quando o shape é desconhecido;
- amplia preflight, artifact validation, smoke routes e regressão offline EKS/AKS/GKE;
- adiciona quality gate contra findings duplicados, severidades conflitantes e `PASS` com baixa confiança;
- registra duração, chamadas/retries/throttling/bytes da Kubernetes API, peak RSS e tamanho da coleta;
- preserva `CANCELLED`/`TIMED_OUT` mesmo quando a interrupção ocorre durante o preflight;
- reforça redaction de logs para credenciais em key-value, auth schemes, JWT, AWS access keys e URLs autenticadas.

## 0.4.0-rc.2 — 2026-08-30

- redesenha o menu terminal com identidade Kubernetes, contexto, versão, total de coletas e estado read-only;
- aplica ao dashboard uma paleta enterprise baseada no azul oficial `#326CE5`;
- incorpora o SVG oficial do Kubernetes mantido no CNCF artwork;
- melhora hierarquia visual, contraste, navegação, cards, tabelas, formulários e responsividade;
- preserva saída sem ANSI quando redirecionada ou quando `NO_COLOR` está definido.

## 0.4.0-rc.1 — 2026-08-30

- adiciona `Events & Diagnostics`, `Versions & Lifecycle` e `Manifest Quality`;
- evolui capacidade para `Container Tuning` orientado por telemetria;
- adiciona engine portátil de `Best Practices` para Kubernetes, EKS, AKS e GKE;
- adiciona logs opcionais com opt-in, targets explícitos, limite e redaction;
- publica o artefato exportável `operational-insights.json`.

## 0.3.0-rc.1 — 2026-08-29

- dashboard portátil com autenticação temporária, progresso, cancelamento e tratamento de porta;
- assessment genérico Kubernetes/EKS read-only com Prometheus opcional;
- CIS Security schema 1.1, 25 controles universais, score por domínio e comparação;
- evidências externas com SHA-256/validade, lifecycle e exceções temporárias;
- relatório executivo imprimível, exportação JSON, checksum e SBOM SPDX;
- fixtures sanitizadas EKS, AKS e GKE para regressão offline.

Gate da RC: validar execução real em EKS, AKS e GKE antes da versão estável. Em 2026-08-30, o ambiente disponível era Kubernetes on-premises; não havia identidade AWS válida nem Azure/Google Cloud CLI, portanto nenhuma validação cloud foi presumida.
