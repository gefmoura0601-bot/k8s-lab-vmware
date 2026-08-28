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

Selecione a opção 5 e abra a URL temporária exibida, normalmente `http://192.168.109.151:8765/?access_token=...`. Não é necessário túnel SSH. Se a porta estiver ocupada por outro dashboard do assessment, o menu permite usar a sessão atual, encerrá-la com `SIGTERM` e iniciar outra na mesma porta, ou escolher automaticamente a próxima porta livre. Um processo desconhecido nunca é encerrado: nesse caso, somente uma nova porta ou o retorno ao menu são oferecidos. Também é possível definir previamente, por exemplo, `DASHBOARD_PORT=8766`.

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

A versão está em `VERSION`. A saída padrão é `${XDG_STATE_HOME:-$PWD}/eks-assessment`, substituível por `ASSESSMENT_ROOT`. Uma distribuição deve conter apenas `bin/`, `src/`, `web/`, `deploy/`, `docs/`, `README.md` e `VERSION`, preservar permissões executáveis e publicar checksum SHA-256 e SBOM do pacote.

Gere o pacote portátil, o checksum e o SBOM SPDX com:

```bash
./bin/package-release.sh ./dist
sha256sum -c ./dist/eks-assessment-*.tar.gz.sha256
```

Extraia o arquivo em qualquer diretório gravável e execute `bin/eks-assessment.sh`. O processo não pressupõe checkout Git nem caminhos como `/workspace`; dependências e permissões são verificadas pelo preflight.
