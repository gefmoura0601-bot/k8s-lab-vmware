# Changelog

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
