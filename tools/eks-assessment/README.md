# Assessment completo de EKS/Kubernetes

O assessment é adaptativo, somente leitura e executável a partir de qualquer host Linux com acesso autorizado às APIs necessárias. Ele não depende de acesso SSH aos nodes nem precisa ser instalado em um node `master` ou no control plane. Ele combina cinco camadas:

1. `assess-eks.sh`: saúde e baseline pontual;
2. `eks-cluster-discovery.sh`: inventário técnico baseado nas salvaguardas do projeto oficial `sample-eks-cluster-discovery-tool`;
3. `aws_eks_assessment.py`: configuração gerenciada do EKS exposta pelas APIs AWS, add-ons, node groups, EKS Cluster Insights, identidade, rede e segurança de conta opcional;
4. `eks_semantic_assessment.py`: análise semântica de workloads, rede, storage/DR, RBAC/admission, autoscaling, operators e supply chain;
5. `eks_comprehensive_assessment.py`: correlação, fingerprints estáveis, recomendações e evidências sanitizadas.

Estados: `CRIT`, `WARN`, `UNKNOWN`, `PARTIAL`, `INFO`, `PASS` e `N/A`. Recurso comprovadamente não aplicável é `N/A`; evidência ausente é `UNKNOWN`; coleta incompleta é `PARTIAL`. Falha de RBAC/API nunca é conformidade. Nenhum componente aplica, altera, reinicia, escala ou exclui recursos.

## Onde executar

Execute a partir da raiz do repositório em qualquer host Linux autorizado, como estação administrativa, bastion, runner de CI/CD ou contêiner operacional, que tenha:

- Bash, Python 3.10+, `kubectl`, `jq`, `curl`, `timeout` e `setsid`;
- kubeconfig/contexto apontando para o cluster e RBAC somente leitura;
- conectividade com a API Kubernetes e, quando utilizado, com o Prometheus;
- AWS CLI e credenciais AWS somente leitura apenas para o enriquecimento específico de EKS.

O host não precisa pertencer ao cluster. Em EKS, o assessment não acessa hosts do control plane, etcd ou processos internos; ele usa a API Kubernetes e, opcionalmente, as configurações gerenciadas expostas pelas APIs AWS.

Secrets e valores de ConfigMap não são solicitados pelo perfil padrão. Essas APIs aparecem como cobertura `PARTIAL` por política de minimização de dados.

## Preflight

Antes de coletar, valide dependências, contexto, API, RBAC e integrações opcionais:

```bash
bash tools/eks-assessment/src/assessment-preflight.sh
```

O menu e o botão web de coleta executam esse preflight automaticamente e não criam uma coleta quando há falha obrigatória. Restrições em APIs opcionais deixam a cobertura `PARTIAL`; ambiente não EKS e Prometheus não configurado ficam `N/A`.

O interpretador Python 3.10+ é selecionado automaticamente. Para fixar um binário compatível:

```bash
PYTHON_BIN=/caminho/python3 bash tools/eks-assessment/bin/eks-assessment.sh
```

## Executar o menu

O menu terminal usa um tema Kubernetes em azul, mostra versão, contexto, porta do dashboard e quantidade de coletas. Cores ANSI são habilitadas somente em terminal interativo. Para desabilitá-las, use `NO_COLOR=1`; para impedir a limpeza de tela, use `ASSESSMENT_MENU_CLEAR=0`.

O menu reúne baseline antes/depois, comparação, dashboard terminal e dashboard web preso à sessão:

```bash
bash tools/eks-assessment/bin/eks-assessment.sh
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

A release `0.4.0-rc.1` adiciona áreas baseadas no mesmo artefato sanitizado:

- **Events & Diagnostics:** Events deduplicados, estado de Pods e troubleshooting, sem persistir mensagens livres;
- **Versions & Lifecycle:** Kubernetes, kubelet, runtime, sistema operacional, kernel, imagens e tecnologias; versão desconhecida permanece `UNKNOWN`;
- **Manifest Quality:** segurança, reliability, scheduling, storage, network e supply chain avaliados sobre objetos da Kubernetes API;
- **Container Tuning:** evolução das propostas de requests/limits, sempre sem alteração automática;
- **Best Practices:** regras genéricas e pacotes EKS, AKS e GKE com aplicabilidade e responsabilidade explícitas.

O artefato fica em `operational-insights.json` e pode ser exportado por `GET /export-operational`.

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

O total planejado é adaptativo: a etapa Prometheus, por exemplo, só participa do cálculo quando uma URL foi configurada. Uma etapa encerrada com erro conta como processada, mas apenas uma coleta com estado final `COMPLETED` chega a 100%. Falhas, cancelamento e timeout preservam o estado final `FAILED`, `CANCELLED` ou `TIMED_OUT` e nunca são apresentados como conclusão bem-sucedida.

A interface consulta `GET /api/collection-status` a cada 750 ms enquanto envia `POST /collect` de forma assíncrona. Se JavaScript estiver indisponível, o envio HTML tradicional continua funcionando como fallback, sem a atualização visual em tempo real. O botão fica desabilitado durante a execução para evitar submissões duplicadas; ao concluir, o navegador abre automaticamente a coleta gerada.

## Permissões mínimas

Exemplos auditáveis ficam em `deploy/`:

- `rbac-namespaced.yaml`: Role para coleta restrita a um namespace;
- `rbac-cluster-readonly.yaml`: ClusterRole para inventário Kubernetes amplo;
- `iam-eks-readonly.json`: APIs AWS/EKS do enriquecimento padrão;
- `iam-account-security-optional.json`: GuardDuty opcional, separado do perfil padrão.

Os exemplos não concedem leitura de Secrets ou ConfigMaps. Substitua os namespaces e vincule as roles somente à identidade aprovada. APIs opcionais sem permissão ficam `PARTIAL` ou `UNKNOWN`.

## Visibilidade por plataforma

- **Amazon EKS:** executa o scan genérico pela API Kubernetes e, quando AWS CLI/credenciais estão disponíveis, usa apenas operações AWS `list`, `describe` e `get` para configuração do cluster, node groups, add-ons e EKS Cluster Insights.
- **Kubernetes autogerenciado/on-premises:** executa o mesmo scan pela API Kubernetes. Objetos do control plane visíveis pela API podem ser inventariados como recursos comuns, sem SSH, leitura de filesystem, acesso direto ao etcd ou inspeção de processos dos hosts.
- **Outros Kubernetes gerenciados:** mantém o scan genérico; verificações exclusivas de AWS/EKS ficam `N/A`, `UNKNOWN` ou `PARTIAL`, conforme aplicabilidade e evidência disponível.

O nome do cluster parte do contexto Kubernetes atual. Para o enriquecimento EKS, ele pode ser obtido do ARN do contexto ou informado por `EKS_CLUSTER_NAME`; a região pode vir do contexto/AWS CLI ou de `AWS_REGION`/`AWS_DEFAULT_REGION`.

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
- `nodes.json`, `pods.json`, `workloads.json`, `namespaces.json`, `pvcs.json`: snapshots com status preservado e valores arbitrários de `env` redatados;
- `events.json`: classificação e timestamps preservados, sem mensagens livres ou UIDs;
- `application-manifests-sanitized.json`: manifests de aplicação sem status/managed fields, valores arbitrários de `env`, dados de Secret ou valores de ConfigMap;
- `api-resources.json`: APIs listáveis descobertas;
- Secrets e valores de ConfigMap: não coletados pelo perfil padrão;
- `prometheus-telemetry.json`: séries agregadas e estatísticas;
- `discovery/`: evidências do discovery oficial;

Valores de tuning explicitamente permitidos (`JAVA_TOOL_OPTIONS`, `JAVA_OPTS`, opções .NET e equivalentes) podem aparecer na evidência para análise. Tokens, senhas e demais variáveis permanecem redatados.

## Versão e distribuição

A versão está em `VERSION`. A saída padrão é `${XDG_STATE_HOME:-$PWD}/eks-assessment`, substituível por `ASSESSMENT_ROOT`. Uma distribuição deve conter apenas `bin/`, `src/`, `web/`, `deploy/`, `docs/`, `README.md`, `CHANGELOG.md` e `VERSION`, preservar permissões executáveis e publicar checksum SHA-256 e SBOM do pacote.

Gere o pacote portátil, o checksum e o SBOM SPDX com:

```bash
./bin/package-release.sh ./dist
sha256sum -c ./dist/eks-assessment-*.tar.gz.sha256
```

Extraia o arquivo em qualquer diretório gravável e execute `bin/eks-assessment.sh`. O processo não pressupõe checkout Git nem caminhos como `/workspace`; dependências e permissões são verificadas pelo preflight.
