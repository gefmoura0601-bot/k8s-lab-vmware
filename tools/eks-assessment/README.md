# Assessment completo de Kubernetes

O assessment é adaptativo, somente leitura e executável a partir de qualquer host Linux com acesso autorizado às APIs necessárias. Ele não depende de acesso SSH aos nodes nem precisa ser instalado em um node `master` ou no control plane. Ele combina camadas independentes e versionadas:

1. `assess-eks.sh`: saúde e baseline pontual;
2. `eks-cluster-discovery.sh`: inventário técnico baseado nas salvaguardas do projeto oficial `sample-eks-cluster-discovery-tool`;
3. `aws_eks_assessment.py`: configuração gerenciada do EKS exposta pelas APIs AWS, add-ons, node groups, EKS Cluster Insights, identidade, rede e segurança de conta opcional;
4. `cloud_provider_assessment.py`: evidência normalizada EKS, AKS ou GKE, sem persistir payloads brutos nem identificadores de conta;
5. `eks_semantic_assessment.py`: análise semântica de workloads, rede, storage/DR, RBAC/admission, autoscaling, operators e supply chain;
6. `operational_insights.py`: Events, lifecycle, Manifest Quality, Container Tuning, Best Practices e logs opcionais;
7. `eks_comprehensive_assessment.py`: correlação, fingerprints estáveis, recomendações e evidências sanitizadas;
8. `provider_validation.py`: gates offline para provider, read-only, aplicabilidade, proteção de dados, cobertura, performance e integridade dos artefatos;
9. `regression_validation.py`: comparação offline por policy entre uma coleta anterior e uma atual, com outputs JSON, JUnit e SARIF.
10. `assessment_contracts.py`: JSON Schemas versionados para os contratos persistidos;
11. `collector_registry.py`: plano de coleta com dependências, pesos, retomada e retry;
12. `manifest_schema_validation.py`: validação estrutural e semântica offline de manifests sanitizados;
13. `collection_bundle.py`: exportação, verificação, importação e retenção segura de coletas;
14. `release_verification.py`: verificação de package, checksum, SBOM, provenance e assinatura opcional;
15. `configuration_metadata.py`: opt-in de nomes/types/keys de ConfigMaps e Secrets, sem persistir values;
16. `blue_green_readiness.py`: Configuration References, Traffic Paths, State & Data e probes de cutover;
17. `migration_compare.py`: Migration Gate source → target com mapping explícito entre clusters e namespaces.

Estados: `CRIT`, `WARN`, `UNKNOWN`, `PARTIAL`, `INFO`, `PASS` e `N/A`. Recurso comprovadamente não aplicável é `N/A`; evidência ausente é `UNKNOWN`; coleta incompleta é `PARTIAL`. Falha de RBAC/API nunca é conformidade. Nenhum componente aplica, altera, reinicia, escala ou exclui recursos.

## Onde executar

Execute a partir da raiz do repositório em qualquer host Linux autorizado, como estação administrativa, bastion, runner de CI/CD ou contêiner operacional, que tenha:

- Bash, Python 3.10+, `kubectl`, `jq`, `curl`, `timeout` e `setsid`;
- kubeconfig/contexto apontando para o cluster e RBAC somente leitura;
- conectividade com a API Kubernetes e, quando utilizado, com o Prometheus;
- AWS CLI, Azure CLI ou Google Cloud CLI e identidade somente leitura apenas para o enriquecimento do provider correspondente.

O host não precisa pertencer ao cluster. Em EKS, o assessment não acessa hosts do control plane, etcd ou processos internos; ele usa a API Kubernetes e, opcionalmente, as configurações gerenciadas expostas pelas APIs AWS.

Secrets e valores de ConfigMap não são solicitados pelo perfil padrão. Nomes, types e keys podem ser validados por opt-in; values nunca são persistidos. Essas APIs aparecem como cobertura `PARTIAL` por política de minimização de dados.

## Preflight

Antes de coletar, valide dependências, contexto, API, RBAC e integrações opcionais:

```bash
bash tools/eks-assessment/src/assessment-preflight.sh
```

O menu e o botão web de coleta executam esse preflight automaticamente e não criam uma coleta quando há falha obrigatória. Restrições em APIs opcionais deixam a cobertura `PARTIAL`; integrações não aplicáveis e Prometheus não configurado ficam `N/A`. EKS, AKS e GKE são detectados por contexto/provider ID; CLIs e escopos cloud são gates opcionais.

O interpretador Python 3.10+ é selecionado automaticamente. Para fixar um binário compatível:

```bash
PYTHON_BIN=/caminho/python3 bash tools/eks-assessment/bin/eks-assessment.sh
```

## Executar o menu

O menu terminal usa um tema Kubernetes em azul, mostra versão, contexto, porta do dashboard e quantidade de coletas. Cores ANSI são habilitadas somente em terminal interativo. Para desabilitá-las, use `NO_COLOR=1`; para impedir a limpeza de tela, use `ASSESSMENT_MENU_CLEAR=0`.

O menu reúne baseline antes/depois, comparação, dashboard terminal, dashboard web preso à sessão, Release Gate pela opção 7, Regression Gate pela opção 8, Blue-Green Readiness pela opção 11 e Migration Gate pela opção 12:

```bash
bash tools/eks-assessment/bin/eks-assessment.sh
```

## Automação headless

Os mesmos fluxos possuem subcomandos sem prompts, adequados a terminal remoto e CI/CD. Uma coleta reproduzível do namespace `banking`, sem Prometheus, pode ser executada assim:

```bash
bash tools/eks-assessment/bin/eks-assessment.sh collect \
  --phase after \
  --change-id banking-release-42 \
  --namespace banking \
  --no-prometheus \
  --root /var/lib/kubernetes-assessment
```

O final da saída inclui `COLLECTION_ID`, `COLLECTION_PATH` e `COLLECTION_STATUS`. Uma coleta `FAILED`, `TIMED_OUT` ou `CANCELLED` retorna exit code não zero. Prometheus não é autodetectado no modo headless: use `--prometheus-url` ou faça opt-in com `--auto-detect-prometheus`.

O `namespaceScope` é persistido na metadata. O Regression Gate bloqueia uma
comparação quando cluster ou namespace scope divergem e não calcula deltas
enganosos para os demais gates.

Exemplos offline, que não exigem `kubectl` nem novas chamadas às APIs:

```bash
bash tools/eks-assessment/bin/eks-assessment.sh list --root assessment
bash tools/eks-assessment/bin/eks-assessment.sh release-gate \
  --root assessment --collection "$COLLECTION_ID" --provider generic-kubernetes
bash tools/eks-assessment/bin/eks-assessment.sh regression-gate \
  --root assessment --before "$BEFORE_ID" --after "$COLLECTION_ID" --profile standard
bash tools/eks-assessment/bin/eks-assessment.sh blue-green-gate \
  --root assessment --collection "$COLLECTION_ID" --probe-url https://green.example.test/health
bash tools/eks-assessment/bin/eks-assessment.sh migration-gate \
  --root assessment --source "$BEFORE_ID" --target "$COLLECTION_ID" \
  --mapping tools/eks-assessment/docs/migration-mapping.example.json
```

Argumentos, exit codes e requisitos por subcomando estão em [`docs/headless-cli.md`](docs/headless-cli.md).

O alias provider-neutral abaixo executa exatamente a mesma CLI. O nome legado é
mantido para compatibilidade:

```bash
bin/kubernetes-assessment --version
```

Para iniciar somente a web:

```bash
python3 tools/eks-assessment/src/assessment_dashboard.py \
  --root assessment \
  --static tools/eks-assessment/web/public \
  --host 127.0.0.1 --port 8765
```

Na execução direta, o servidor permanece restrito a loopback. A opção 5 do menu faz exposição explícita em todas as interfaces, gera um access token temporário, mostra a URL de entrada e mantém o processo preso ao terminal. O token é removido da URL após o primeiro acesso e trocado por um cookie `HttpOnly` com `SameSite=Strict`; ele deixa de valer quando `Ctrl+C` encerra o processo. Não há PID file ou processo em background. Use essa opção somente na rede privada do lab. O namespace escolhido é propagado a todas as consultas namespaced. A URL Prometheus é opcional; credenciais, redirects, loopback, link-local e metadata endpoints são rejeitados. `PROMETHEUS_ALLOWED_HOSTS` restringe opcionalmente os hosts aceitos.

No control plane do lab, preserve o diretório compartilhado de coletas e execute:

```bash
cd /workspace/tools/eks-assessment
ASSESSMENT_ROOT=/workspace/assessment PYTHON_BIN=python3.11 ./bin/eks-assessment.sh
```

Selecione a opção 5 e abra uma das URLs temporárias exibidas. O menu sempre inclui `127.0.0.1` para execução local; em uma sessão SSH, prioriza o IP do servidor informado por `SSH_CONNECTION`; interfaces IPv4 adicionais aparecem como alternativas, com redes típicas de containers filtradas. `DASHBOARD_PUBLIC_HOST=dashboard.empresa.local` define explicitamente o endereço prioritário, sem remover as alternativas, e `DASHBOARD_PORT=8766` altera a porta. Não é necessário túnel SSH quando existe conectividade direta. Se a porta estiver ocupada por outro dashboard do assessment, o menu permite usar a sessão atual, encerrá-la com `SIGTERM` e iniciar outra na mesma porta, ou escolher automaticamente a próxima porta livre. Um processo desconhecido nunca é encerrado: nesse caso, somente uma nova porta ou o retorno ao menu são oferecidos.

## Progresso da coleta web

O dashboard usa o ícone oficial do Kubernetes e a cor primária `#326CE5`, mantendo o nome **Kubernetes Assessment Console** para não sugerir certificação ou endosso. Origem e regras de uso estão documentadas em [`docs/branding.md`](docs/branding.md).

### Operational Insights

A série `0.5.0-rc` amplia as áreas baseadas no mesmo artefato sanitizado:

- **Events & Diagnostics:** Events deduplicados, estado de Pods e troubleshooting, sem persistir mensagens livres;
- **Operational Timeline:** cronologia sanitizada de Events, restarts e mudanças de condição dos nodes;
- **Node Health:** uso total, requests, reserva, densidade de Pods, condições de pressão e decomposição entre Kubernetes/System Pods, DaemonSets, workloads e `Node overhead / não atribuído`, usando Kubernetes API e Metrics API;
- **Versions & Lifecycle:** Kubernetes, kubelet, runtime, sistema operacional, kernel, imagens e tecnologias, com catálogo oficial versionado; `UNKNOWN` indica estado indeterminado e `EVIDENCE_UNAVAILABLE` indica componente detectado sem fonte confiável de lifecycle;
- **Manifest Quality:** segurança, reliability, scheduling, storage, network e supply chain, mais validação estrutural/semântica offline; sem OpenAPI ou server-side dry-run o estado máximo dessa camada é `PARTIAL`;
- **Container Tuning:** evolução das propostas de requests/limits, sempre sem alteração automática;
- **Best Practices:** regras genéricas e pacotes EKS, AKS e GKE com aplicabilidade e responsabilidade explícitas.

O artefato fica em `operational-insights.json` e pode ser exportado por `GET /export-operational`.

O dashboard organiza a navegação pela finalidade de cada página:

| Área | Páginas |
|---|---|
| Visão geral | Visão geral, Assessment, Problemas, Busca global |
| Inventário | Nodes, Namespaces, Workloads, Tecnologias, Versions & Lifecycle, RabbitMQ |
| Observabilidade | Events & Diagnostics, Operational Timeline, Logs, Prometheus |
| Capacidade e saúde | Node Health, Container Tuning |
| Segurança | CIS Security, Relatório executivo CIS |
| Governança | Best Practices, Manifest Quality, Release Gate, Regression Gate |
| Migração | Blue-Green Readiness, incluindo a comparação Migration Gate source → target |
| Plataformas | Cloud Provider, AWS / EKS detalhado |
| Coletas e relatórios | Nova coleta, Cobertura da coleta, Comparar coletas |

A busca global consulta esses domínios; conteúdo de logs não é indexado. No terminal, as opções ficam em Coletas, Visualização e relatórios, Governança e Migração, mantendo a numeração existente.

Logs permanecem desabilitados por padrão. Para coleta explícita:

```bash
ASSESSMENT_INCLUDE_LOGS=1 \
ASSESSMENT_LOG_TARGETS='apps/deployment/minha-api:app' \
ASSESSMENT_LOG_MAX_BYTES=262144 \
bash bin/eks-assessment.sh
```

O target usa `namespace/kind/name[:container]`, limitado a Pod, Deployment, StatefulSet e DaemonSet. A coleta usa uma hora/200 linhas, aplica redaction e nunca usa ausência de logs para produzir `PASS`. Veja [`docs/roadmap-operational-insights.md`](docs/roadmap-operational-insights.md).

Ao iniciar **Nova coleta** ou **Novo baseline**, o dashboard mantém a página aberta e apresenta uma barra de progresso baseada nas etapas efetivamente encerradas pelo supervisor. A atualização ocorre automaticamente e informa:

- percentual concluído;
- componente em execução, como preflight, assessment, discovery, inventários, Prometheus, comprehensive assessment e artifact validation;
- quantidade de etapas concluídas e total planejado para aquela coleta;
- limite máximo de tempo restante.

O total planejado é adaptativo e ponderado pelo custo de cada collector: Prometheus e Node Evidence, por exemplo, só participam quando uma URL foi configurada. O plano e o estado de cada collector ficam em `collector-state.json`. Uma etapa encerrada com erro conta como processada, mas apenas uma coleta com estado final `COMPLETED` chega a 100%. Falhas, cancelamento e timeout preservam o estado final `FAILED`, `CANCELLED` ou `TIMED_OUT` e nunca são apresentados como conclusão bem-sucedida.

A interface consulta `GET /api/collection-status` a cada 750 ms enquanto envia `POST /collect` de forma assíncrona. Se JavaScript estiver indisponível, o envio HTML tradicional continua funcionando como fallback, sem a atualização visual em tempo real. O botão fica desabilitado durante a execução para evitar submissões duplicadas; ao concluir, o navegador abre automaticamente a coleta gerada.

## Permissões mínimas

Exemplos auditáveis ficam em `deploy/`:

- `rbac-namespaced.yaml`: Role para coleta restrita a um namespace;
- `rbac-cluster-readonly.yaml`: ClusterRole para inventário Kubernetes amplo;
- `iam-eks-readonly.json`: APIs AWS/EKS do enriquecimento padrão;
- `iam-account-security-optional.json`: GuardDuty opcional, separado do perfil padrão.
- `azure-aks-assessment-readonly-role.json`: Azure RBAC custom role somente leitura para configuração, node pools, upgrade profile e versões regionais;
- `gcp-gke-assessment-readonly-role.yaml`: custom role GCP mínima para leitura do cluster e server config.
- `rbac-configuration-metadata-namespaced.yaml`: Roles opcionais e separadas para ConfigMap e Secret metadata.

O ClusterRole inclui `get/list` em `nodes` e `pods` de `metrics.k8s.io`. A Role namespaced inclui apenas Pod metrics; sem acesso cluster-scoped a nodes e Node metrics, `Node Health` permanece `PARTIAL` ou `EVIDENCE_UNAVAILABLE`, nunca `PASS`.

Os perfis padrão não concedem leitura de Secrets ou ConfigMaps. O exemplo adicional é opt-in, namespaced e separa as duas permissões para que somente o RoleBinding necessário seja aplicado. Mesmo sem persistir values, `get/list` entrega o objeto completo ao processo; use identidade temporária e namespace mínimo. APIs opcionais sem permissão ficam `PARTIAL` ou `UNKNOWN`.

## Visibilidade por plataforma

- **Amazon EKS:** executa o scan genérico pela API Kubernetes e, quando AWS CLI/credenciais estão disponíveis, usa apenas operações AWS `list`, `describe` e `get` para configuração do cluster, node groups, add-ons e EKS Cluster Insights.
- **Azure AKS:** com `AKS_CLUSTER_NAME` e `AKS_RESOURCE_GROUP`, usa `az aks show/get-upgrades/nodepool list/get-versions`; o control plane permanece `MANAGED_PROVIDER`.
- **Google GKE:** detecta o contexto padrão ou usa `GKE_CLUSTER_NAME`, `GKE_LOCATION` e `GCP_PROJECT`; consulta somente `clusters describe` e `get-server-config`.
- **Kubernetes autogerenciado/on-premises:** executa o mesmo scan pela API Kubernetes. Objetos do control plane visíveis pela API podem ser inventariados como recursos comuns, sem SSH, leitura de filesystem, acesso direto ao etcd ou inspeção de processos dos hosts.
- **Outros Kubernetes gerenciados:** mantém o scan genérico; verificações exclusivas de AWS/EKS ficam `N/A`, `UNKNOWN` ou `PARTIAL`, conforme aplicabilidade e evidência disponível.

O nome do cluster parte do contexto Kubernetes atual. Para o enriquecimento EKS, ele pode ser obtido do ARN do contexto ou informado por `EKS_CLUSTER_NAME`; a região pode vir do contexto/AWS CLI ou de `AWS_REGION`/`AWS_DEFAULT_REGION`. Os escopos AKS/GKE e exemplos read-only estão documentados em [`docs/cloud-provider-evidence.md`](docs/cloud-provider-evidence.md).

## CIS Security

A aba **CIS Security** apresenta uma avaliação de postura baseada na família [CIS Kubernetes Benchmarks](https://www.cisecurity.org/benchmark/kubernetes). A referência atual é Kubernetes 2.0.1 e os perfis gerenciados EKS, AKS e GKE 2.0.0. Essa funcionalidade não é CIS-CAT, não é certificada pelo CIS e não representa certificação nem compliance integral.

Cada controle registra `evidenceSource`, `applicability`, `assessmentMode`, `managedResponsibility`, `status`, evidência sanitizada e recomendação. As origens suportadas são `KubernetesAPI`, `CloudProviderAPI`, `NodeEvidence`, `ControlPlaneEvidence` e `ManualEvidence`. Os estados de aplicabilidade são:

- `APPLICABLE`;
- `NOT_APPLICABLE`;
- `MANAGED_PROVIDER`;
- `EVIDENCE_UNAVAILABLE`;
- `MANUAL_REVIEW`.

A responsabilidade é `CUSTOMER`, `CLOUD_PROVIDER` ou `SHARED`. O score inclui somente controles automatizados, aplicáveis e atribuídos ao cliente ou de responsabilidade compartilhada. Controles gerenciados pelo provider, manuais ou sem evidência não contam como `PASS` e não reduzem artificialmente o score.

A cobertura automatizada inicial inclui RBAC wildcard, bindings `cluster-admin`, leitura de Secrets, impersonation, containers privilegiados, capabilities, privilege escalation, namespaces do host, `runAsNonRoot`, seccomp, root filesystem somente leitura, default ServiceAccount, tags e digests de imagens, Services externos, NetworkPolicy, admission policies e Pod Security Admission. Em EKS, AKS e GKE, kube-apiserver e etcd aparecem como `MANAGED_PROVIDER`; em self-managed, ficam `EVIDENCE_UNAVAILABLE` até que evidência autorizada seja fornecida. Nenhuma regra exige SSH, `/etc/kubernetes`, filesystem do node ou acesso direto ao control plane.

A interface oferece filtros por status, aplicabilidade, responsabilidade, Evidence Source e texto livre. Cada controle é expansível para mostrar evidência sanitizada e recomendação. Cards separados destacam `MANAGED_PROVIDER`, `MANUAL_REVIEW` e `EVIDENCE_UNAVAILABLE`. A opção **Exportar relatório CIS JSON** baixa somente o relatório CIS da coleta selecionada.

O dashboard separa **Posture Score** de **Evidence Coverage**. O primeiro pondera controles comprovados por risco e apresenta score por domínio; o segundo mostra a proporção da responsabilidade do cliente que possui evidência automatizada suficiente. Perder acesso a uma API pode reduzir Evidence Coverage, mas nunca é apresentado como melhoria de postura.

O plano de ação ordena controles `WARN` por prioridade, impacto e esforço. Cada item inclui recomendação, comando read-only para nova validação e exemplo declarativo de remediação que não é aplicado automaticamente. A comparação CIS entre duas coletas classifica `REGRESSION`, `RESOLVED`, `EVIDENCE_LOSS`, `COVERAGE_GAIN`, mudanças de responsabilidade/aplicabilidade e controles adicionados ou removidos.

Evidências externas, lifecycle e exceções temporárias usam arquivos JSON opcionais documentados em [`docs/cis-evidence.md`](docs/cis-evidence.md). O relatório executivo é uma página sanitizada e preparada para **Imprimir → Salvar como PDF**, com scores, matriz de responsabilidade e plano de ação. A engine reutiliza evidências AWS/EKS já coletadas e aceita o mesmo contrato para AKS/GKE, sem executar SSH ou chamadas mutáveis.

O relatório estruturado é gravado em `cis-security-assessment.json` e também referenciado por `comprehensive-assessment.json`.

## Limites e cancelamento

- perfis Web: baixo impacto 15 min, conservador 30 min e exaustivo 60 min;
- no menu, `ASSESSMENT_MAX_DURATION_SECONDS` define o teto total (padrão 1800s; faixa 60–7200s);
- `Ctrl+C`, `SIGTERM`, o botão **Cancelar coleta** ou a saída do menu encerram o grupo completo de processos;
- após TERM há 10s de graça e então KILL, evitando `kubectl`, `aws`, helpers ou port-forwards órfãos;
- artefatos parciais são preservados com estado `CANCELLED` ou `TIMED_OUT`, nunca como coleta concluída.

Cada coleta registra `metadata.performance.durationSeconds`; a web também registra duração por componente. `comprehensive-assessment.json.performance` informa chamadas/retries/throttling/bytes da Kubernetes API, peak RSS do processo e tamanho da coleta antes do relatório. Isso permite calibrar impacto sem persistir conteúdo adicional.

Smoke de cancelamento real: `bash tools/eks-assessment/tests/smoke-assessment-cancellation.sh`.

## Scanner direto

Para analisar uma coleta existente sem consultar novamente o cluster:

```bash
python3 tools/eks-assessment/src/eks_comprehensive_assessment.py \
  --snapshot-dir assessment/<coleta>
```

`--resume` só reutiliza snapshots quando `collection-provenance.json` confirma schema, contexto, endpoint, escopo e hashes. Qualquer divergência interrompe o resume.

Para atualizar o inventário read-only antes da análise:

```bash
python3 tools/eks-assessment/src/eks_comprehensive_assessment.py \
  --snapshot-dir assessment/<coleta> \
  --collect-live --timeout 30 --chunk-size 200 \
  --inventory-workers 4 --api-delay-ms 100 \
  --max-requests 1500 --max-duration 3600 \
  --max-response-mb 512 --resume
```

## Cobertura

- saúde de nodes, pods, eventos, restarts e estados de containers;
- uso e capacidade dos nodes com decomposição provider-neutral baseada na Metrics API; detalhes e limitações estão em [`docs/node-health.md`](docs/node-health.md);
- Deployments, StatefulSets, DaemonSets, Jobs, CronJobs, Rollouts e Pods independentes;
- requests/limits, probes, init containers e ephemeral containers;
- HPA, VPA, KEDA, PDB, réplicas, topology spread, anti-affinity e conflitos entre autoscalers;
- NetworkPolicy, PSS, securityContext, seccomp, capabilities, host namespaces/hostPath;
- ServiceAccounts, RBAC, wildcards, cluster-admin, admission e policies;
- Services/EndpointSlices, Ingress/backends, Gateway API, Istio, PVC/PV, StorageClass, VolumeSnapshot e evidência Velero;
- Argo CD, Kyverno, Karpenter, cert-manager, External Secrets, Velero, Strimzi, RabbitMQ Cluster Operator, CloudNativePG, ServiceMonitor, PodMonitor e PrometheusRule quando instalados;
- imagens mutáveis, tags/digests e configuração de supply chain observável;
- scan ECR read-only das imagens utilizadas, evidência de vulnerabilidade e lacunas de assinatura/SBOM;
- backup/restore, snapshots, PVC/PV órfãos, LoadBalancers sem endpoints, fragmentação de requests e prontidão Spot;
- detecção e checks específicos para Java, .NET, Kafka, RabbitMQ, Nginx, API gateways, PostgreSQL e Redis;
- inventário de todas as APIs listáveis, com budget, retry/backoff, limite de resposta, retomada e escopo opcional;

A detecção de tecnologia usa imagem, nome, comando e variáveis de tuning permitidas; deve ser confirmada por versão/runtime ou SBOM. Recomendações de Java/.NET são condicionais à versão e à telemetria, não alterações automáticas.

## Prometheus e capacidade

`prometheus_telemetry.py` usa somente `GET`. Quando `PROMETHEUS_URL` não estiver definido, o menu e o formulário web procuram Services read-only com identidade Prometheus e sugerem o `ClusterIP`/port detectado; o operador pode aceitar, editar ou remover a sugestão. O endpoint usado continua explícito. O catálogo de métricas e os labels são descobertos automaticamente por `/api/v1/label/__name__/values`, `/api/v1/metadata` e `/api/v1/series`; as estatísticas vêm de `/api/v1/query_range`. Não existem nomes fixos de namespace, aplicação ou label.

O baseline opcional via proxy de Service só é consultado quando `PROMETHEUS_NAMESPACE` e `PROMETHEUS_SERVICE` forem ambos informados. Não há namespace ou Service padrão do lab; a telemetria principal continua usando somente a URL explícita.

Ele suporta `1d`, `3d`, `7d`, `14d` e `30d` e coleta por Deployment:

- CPU, memory usage, working set, throttling, reinícios, OOM e tráfego de rede;
- JVM: versão, heap usado/máximo, GC, threads, alocação e memória nativa;
- .NET: versão, managed heap/máximo, GC, thread pool, alocação, exceções e working set;
- Kafka: partições sub-replicadas/offline, lag e erros quando exportados;

O runtime é correlacionado por manifest e por séries reais do Prometheus. Opções seguras de JVM/.NET aparecem sanitizadas na aba Prometheus; segredos e variáveis arbitrárias continuam redigidos. Ausência de série é `N/A`, nunca conformidade.

As propostas de requests/limits comparam valores atuais com p90/p99 e headroom. Elas incluem confiança, quantidade de amostras, indicação HPA/KEDA e ressalvas sobre startup, sazonalidade, sidecars, caches, memória nativa e throttling. São propostas para validação, nunca mudanças aplicadas.

## Artefatos e proteção de dados

- `comprehensive-assessment.json`: checks, fingerprints, cobertura, tecnologias, semântica, AWS/EKS e capacidade;
- `aws-eks-assessment.json`: configuração gerenciada exposta pelas APIs AWS/EKS, node groups, add-ons, identidade, rede e cobertura das APIs; não contém inspeção direta do control plane;
- `cloud-provider-assessment.json`: contrato normalizado EKS/AKS/GKE, lifecycle e Best Practices comprováveis, sem payload cloud bruto ou identificador de conta;
- `operational-insights.json`: Events, Node Health, Versions & Lifecycle, Manifest Quality, Best Practices, Container Tuning e logs opcionais sanitizados;
- `manifest-schema-validation.json`: validação offline estrutural, API servida e semântica básica dos manifests;
- `node-process-evidence.json`: atribuição avançada opcional de CPU/memória via Prometheus, sem SSH;
- `collector-state.json`: plano, tentativas, estados e progresso ponderado dos collectors;
- `contract-validation.json`: resultado da validação por JSON Schemas versionados;
- `provider-validation.json`: gate offline opcional para promover a release em um provider explicitamente esperado;
- `provider-validation.junit.xml`, `provider-validation.sarif.json` e `provider-validation.md`: formatos adicionais do Release Gate;
- `regression-validation.json`: comparação offline orientada pela policy entre baseline e coleta atual;
- `regression-validation.junit.xml` e `regression-validation.sarif.json`: resultados bloqueantes para CI/CD;
- `configuration-metadata.json`: artefato opt-in com nomes/types/keys, sem values ou annotations;
- `configuration-references.json`: referências de workloads, TLS, ConfigMaps e Secrets com estado de resolução;
- `traffic-paths.json`: Ingress, Gateway API, Istio, OpenShift Route, Services e EndpointSlices;
- `state-data-readiness.json`: PVCs, snapshots, backup/restore, replicação, schema, jobs e rollback;
- `migration-probes.json` e `blue-green-readiness.json`: probes explícitos e decisão `GO`/`NO_GO`/`UNKNOWN` por coleta;
- `migration-comparison.json`, `.junit.xml`, `.sarif.json` e `.md`: Migration Gate source → target;
- `nodes.json`, `pods.json`, `workloads.json`, `namespaces.json`, `pvcs.json`: snapshots com status preservado e valores arbitrários de `env` redatados;
- `node-metrics.json`, `pod-metrics.json`: uso pontual sanitizado da Metrics API; quando indisponível, os arquivos ficam vazios e a cobertura é declarada incompleta;
- `events.json`: classificação e timestamps preservados, sem mensagens livres ou UIDs;
- `application-manifests-sanitized.json`: manifests de aplicação sem status/managed fields, valores arbitrários de `env`, dados de Secret ou valores de ConfigMap;
- `api-resources.json`: APIs listáveis descobertas;
- Secrets e valores de ConfigMap: não coletados pelo perfil padrão;
- `prometheus-telemetry.json`: séries agregadas e estatísticas;
- `discovery/`: evidências do discovery oficial;

Valores de tuning explicitamente permitidos (`JAVA_TOOL_OPTIONS`, `JAVA_OPTS`, opções .NET e equivalentes) podem aparecer na evidência para análise. Tokens, senhas e demais variáveis permanecem redatados.

## Provider Validation Runner

Depois de uma coleta completa, valide o ambiente esperado sem executar novas chamadas ao cluster ou ao cloud provider:

```bash
python3 tools/eks-assessment/src/provider_validation.py \
  --collection assessment/<coleta> \
  --expected-provider eks
```

Providers aceitos: `eks`, `aks`, `gke` e `generic-kubernetes`. O runner exige estado `COMPLETED`, cruza três fontes de detecção e bloqueia mutações, evidência parcial, provider divergente, dados sensíveis, baixa cobertura e budgets excedidos. `WARN` mantém `releaseReady=false`. Contrato, thresholds e matriz real estão em [`docs/provider-validation.md`](docs/provider-validation.md).

No menu, use a opção 7, selecione a coleta e informe o provider esperado. No dashboard, abra **Governança → Release Gate**, escolha explicitamente o provider e execute a validação offline. A página apresenta cada gate e sua evidência sanitizada, permite busca global e exporta JSON, JUnit, SARIF e Markdown. A execução web exige autenticação e action token, compartilha o lock da coleta e substitui os relatórios por operação atômica. Os thresholds configuráveis permanecem disponíveis pelo CLI.

## Contratos e bundles portáteis

Valide uma coleta, exporte somente arquivos sanitizados, verifique o bundle e
importe-o em outro host:

```bash
bin/kubernetes-assessment validate --root ./assessment --collection "$COLLECTION_ID"
bin/kubernetes-assessment bundle export --root ./assessment \
  --collection "$COLLECTION_ID" --output "./${COLLECTION_ID}.tar.gz"
bin/kubernetes-assessment bundle verify --bundle "./${COLLECTION_ID}.tar.gz"
bin/kubernetes-assessment bundle import --root ./assessment-importado \
  --bundle "./${COLLECTION_ID}.tar.gz"
```

O bundle rejeita path traversal, links, arquivos extras, checksum divergente e
coleção inválida. Logs não são exportados. `prune --older-than 30d` é dry-run;
somente `--confirm` remove diretórios e baselines continuam protegidos, salvo
`--include-baselines`. Detalhes estão em
[`docs/portable-collections.md`](docs/portable-collections.md).

## Regression Gate

Para comparar duas coletas do mesmo cluster sem consultar novamente nenhuma API:

```bash
python3 tools/eks-assessment/src/regression_validation.py \
  --before assessment/<coleta-anterior> \
  --after assessment/<coleta-atual> \
  --profile standard
```

O profile `standard` bloqueia regressões novas sem exigir a eliminação imediata de riscos legados. O profile `strict` também exige ausência de `CRIT`, `WARN`, `UNKNOWN` e `PARTIAL` nos findings atuais. A policy versionada fica em `data/assessment-policy.json`; uma policy alternativa pode ser informada por `--policy` ou `ASSESSMENT_POLICY_FILE`.

O fluxo também está na opção 8 do menu e em **Governança → Regression Gate**. Ele cruza fingerprints de findings, CIS, Node Health, Manifest Quality, lifecycle, quality gate e impacto da coleta. Identidade divergente, coleta incompleta, artefato inválido ou evidência obrigatória ausente nunca produz `PASS`. Os resultados são exportados como JSON, JUnit e SARIF. Contrato, thresholds e limitações estão em [`docs/regression-validation.md`](docs/regression-validation.md).

## Blue-Green Readiness e Migration Gate

A opção 11 e **Migração → Blue-Green Readiness** avaliam uma coleta sem alterar o cluster. A opção 12 e o subcomando `migration-gate` comparam source e target, inclusive em clusters e namespaces diferentes, desde que o mapping seja explícito. No dashboard, essa comparação fica na seção **Migration Gate source → target** da mesma página. Os gates cobrem workloads, Services, Configuration References, Traffic Paths, NetworkPolicy/PDB/autoscaling, ServiceAccounts, API/lifecycle, storage/dados, Node Health, CIS, observabilidade, probes, DNS/load balancer e rollback.

ConfigMap/Secret metadata é opt-in com `--configmap-metadata` e `--secret-metadata` e exige `--namespace` explícito; coleta cluster-wide desses objetos é bloqueada. DNS, consistência de dados e rollback usam `migration-evidence.json`; ausência de evidência obrigatória resulta em `UNKNOWN`, nunca `GO`. O desenho, exemplos, RBAC e limitações estão em [`docs/blue-green-migration.md`](docs/blue-green-migration.md).

Em coletas namespaced, referências para outro namespace ficam `UNKNOWN` quando a evidência externa não foi coletada; elas não viram `FAIL` artificial. O Migration Gate não replica o `context` Kubernetes em seus relatórios, e URLs de probe inválidas são sanitizadas antes de qualquer persistência.

## Versão e distribuição

A versão está em `VERSION`. A saída padrão é `${XDG_STATE_HOME:-$PWD}/eks-assessment`, substituível por `ASSESSMENT_ROOT`. Uma distribuição deve conter apenas `bin/`, `src/`, `data/`, `web/`, `deploy/`, `docs/`, `README.md`, `CHANGELOG.md` e `VERSION`, preservar permissões executáveis e publicar checksum SHA-256 e SBOM do pacote.

A versão `0.5.0-rc.2` mantém estável o profile `generic-kubernetes`. As integrações
EKS, AKS e GKE permanecem `PREVIEW` até qualificação read-only em clusters reais;
fixtures offline validam contratos, mas não equivalem a suporte operacional. O
estado versionado de cada profile está em `data/release-qualification.json`.

Gere o pacote portátil, o checksum e o SBOM SPDX com:

```bash
./bin/package-release.sh ./dist
(cd ./dist && sha256sum -c eks-assessment-*.tar.gz.sha256)
```

O diretório de saída contém tarball, checksum relativo, SBOM SPDX e provenance
externa. O mesmo `SBOM.spdx` permanece no pacote. Verifique todos os vínculos:

```bash
bin/kubernetes-assessment verify-release \
  --archive dist/eks-assessment-0.5.0-rc.2.tar.gz \
  --checksum dist/eks-assessment-0.5.0-rc.2.tar.gz.sha256 \
  --sbom dist/eks-assessment-0.5.0-rc.2.spdx \
  --provenance dist/eks-assessment-0.5.0-rc.2.provenance.json
```

No GitHub Actions, o tarball também recebe uma attestation Sigstore/SLSA. Uma
assinatura local opcional pode ser produzida com `COSIGN_KEY` e verificada por
`--sigstore-bundle`; `--require-signature` torna sua ausência bloqueante.

Extraia o arquivo em qualquer diretório gravável e execute `bin/eks-assessment.sh`. O processo não pressupõe checkout Git nem caminhos como `/workspace`; dependências e permissões são verificadas pelo preflight.
