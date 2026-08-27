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

- Bash, Python 3.11+, `kubectl`, `jq`, `curl`, `timeout` e `setsid`;
- kubeconfig/contexto apontando para o cluster e RBAC somente leitura;
- conectividade com a API Kubernetes e, quando utilizado, com o Prometheus;
- AWS CLI e credenciais AWS somente leitura apenas para o enriquecimento específico de EKS.

O host não precisa pertencer ao cluster. Em EKS, o assessment não acessa hosts do control plane, etcd ou processos internos; ele usa a API Kubernetes e, opcionalmente, as configurações gerenciadas expostas pelas APIs AWS.

## Executar o menu

O menu reúne baseline antes/depois, comparação, dashboard terminal e dashboard web:

```bash
bash tools/eks-assessment/bin/eks-assessment.sh
```

Para iniciar somente a web:

```bash
python3.11 tools/eks-assessment/src/assessment_dashboard.py \
  --root assessment \
  --static tools/eks-assessment/web/public \
  --host 0.0.0.0 --port 8765
```

Abra `http://<host-de-execucao>:8765`. Se a porta não estiver diretamente acessível, use o túnel ou encaminhamento aprovado para o ambiente. O botão **Coletar agora** executa o pipeline completo. Escolha o perfil de impacto, um namespace opcional ou o cluster inteiro. A URL Prometheus é opcional, mas precisa ser explicitamente informada; usuário/senha embutidos na URL são rejeitados.

## Visibilidade por plataforma

- **Amazon EKS:** executa o scan genérico pela API Kubernetes e, quando AWS CLI/credenciais estão disponíveis, usa apenas operações AWS `list`, `describe` e `get` para configuração do cluster, node groups, add-ons e EKS Cluster Insights.
- **Kubernetes autogerenciado/on-premises:** executa o mesmo scan pela API Kubernetes. Objetos do control plane visíveis pela API podem ser inventariados como recursos comuns, sem SSH, leitura de filesystem, acesso direto ao etcd ou inspeção de processos dos hosts.
- **Outros Kubernetes gerenciados:** mantém o scan genérico; verificações exclusivas de AWS/EKS ficam `N/A`, `UNKNOWN` ou `PARTIAL`, conforme aplicabilidade e evidência disponível.

O nome do cluster parte do contexto Kubernetes atual. Para o enriquecimento EKS, ele pode ser obtido do ARN do contexto ou informado por `EKS_CLUSTER_NAME`; a região pode vir do contexto/AWS CLI ou de `AWS_REGION`/`AWS_DEFAULT_REGION`.

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
python3.11 tools/eks-assessment/src/eks_comprehensive_assessment.py \
  --snapshot-dir assessment/<coleta>
```

Para atualizar o inventário read-only antes da análise:

```bash
python3.11 tools/eks-assessment/src/eks_comprehensive_assessment.py \
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

`prometheus_telemetry.py` usa somente `GET`. O endpoint é informado explicitamente, mas o catálogo de métricas e os labels são descobertos automaticamente por `/api/v1/label/__name__/values`, `/api/v1/metadata` e `/api/v1/series`; as estatísticas vêm de `/api/v1/query_range`. Não existem nomes fixos de namespace, aplicação ou label.

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
- `secrets-metadata.json`: somente metadados, tipo e nomes das chaves;
- `prometheus-telemetry.json`: séries agregadas e estatísticas;
- `discovery/`: evidências do discovery oficial;

Valores de tuning explicitamente permitidos (`JAVA_TOOL_OPTIONS`, `JAVA_OPTS`, opções .NET e equivalentes) podem aparecer na evidência para análise. Tokens, senhas e demais variáveis permanecem redatados.
