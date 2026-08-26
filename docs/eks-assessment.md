# Assessment completo de EKS/Kubernetes

O assessment é adaptativo, somente leitura e executável no node master. Ele combina três camadas:

1. `assess-eks.sh`: saúde e baseline pontual;
2. `eks-cluster-discovery.sh`: inventário técnico em 49 seções, baseado nas salvaguardas do projeto oficial `sample-eks-cluster-discovery-tool`;
3. `eks_comprehensive_assessment.py`: correlação por workload e container, classificação `CRIT/WARN/INFO/PASS/N/A`, recomendações e evidências sanitizadas.

APIs, CRDs e tecnologias inexistentes são `N/A`. Falha de RBAC/API é `UNAVAILABLE`; não é tratada como conformidade. Nenhum componente aplica, altera, reinicia, escala ou exclui recursos.

## Uso recomendado no master

O menu reúne baseline antes/depois, comparação, dashboard terminal e dashboard web:

```bash
bash /workspace/scripts/validation/eks-assessment-menu.sh
```

Para iniciar somente a web:

```bash
python3.11 /workspace/scripts/validation/assessment_dashboard.py \
  --root /workspace/assessment \
  --static /workspace/app/eks-assessment-dashboard/public \
  --host 0.0.0.0 --port 8765
```

Abra `http://<ip-do-master>:8765`. O botão **Coletar agora** executa o pipeline completo. A URL Prometheus é opcional, mas precisa ser explicitamente informada; usuário/senha embutidos na URL são rejeitados.

## Scanner direto

Para analisar uma coleta existente sem consultar novamente o cluster:

```bash
python3.11 /workspace/scripts/validation/eks_comprehensive_assessment.py \
  --snapshot-dir /workspace/assessment/<coleta>
```

Para atualizar o inventário read-only antes da análise:

```bash
python3.11 /workspace/scripts/validation/eks_comprehensive_assessment.py \
  --snapshot-dir /workspace/assessment/<coleta> \
  --collect-live --timeout 30 --chunk-size 200
```

## Cobertura

- saúde de nodes, pods, eventos, restarts e estados de containers;
- Deployments, StatefulSets, DaemonSets, Jobs, CronJobs, Rollouts e Pods independentes;
- requests/limits, probes, init containers e ephemeral containers;
- HPA, VPA, KEDA, PDB, réplicas, topology spread e anti-affinity;
- NetworkPolicy, PSS, securityContext, seccomp, capabilities, host namespaces/hostPath;
- ServiceAccounts, RBAC, wildcards, cluster-admin, admission e policies;
- Services, Ingress, Gateway API, Istio, PVC/PV, StorageClass e VolumeSnapshot;
- Argo CD, Kyverno, Karpenter, ServiceMonitor, PodMonitor e PrometheusRule quando instalados;
- imagens mutáveis, tags/digests e configuração de supply chain observável;
- detecção e checks específicos para Java, .NET, Kafka, RabbitMQ, Nginx, API gateways, PostgreSQL e Redis;
- inventário de todas as APIs listáveis e integração com as 49 seções do discovery.

A detecção de tecnologia usa imagem, nome, comando e variáveis de tuning permitidas; deve ser confirmada por versão/runtime ou SBOM. Recomendações de Java/.NET são condicionais à versão e à telemetria, não alterações automáticas.

## Prometheus e capacidade

`prometheus_telemetry.py` usa somente `GET`. O endpoint é informado explicitamente, mas o catálogo de métricas e os labels são descobertos automaticamente por `/api/v1/label/__name__/values`, `/api/v1/metadata` e `/api/v1/series`; as estatísticas vêm de `/api/v1/query_range`. Não existem nomes fixos de namespace, aplicação ou label.

Ele suporta `1d`, `3d`, `7d`, `14d` e `30d` e coleta por Deployment:

- CPU, memory usage, working set e throttling;
- JVM: versão, heap usado/máximo, GC, threads, alocação e memória nativa;
- .NET: versão, managed heap/máximo, GC, thread pool, alocação, exceções e working set;
- média, pico, p50, p90, p95 e p99, registrando a métrica-fonte e os labels usados.

O runtime é correlacionado por manifest e por séries reais do Prometheus. Opções seguras de JVM/.NET aparecem sanitizadas na aba Prometheus; segredos e variáveis arbitrárias continuam redigidos. Ausência de série é `N/A`, nunca conformidade.

As propostas de requests/limits comparam valores atuais com p90/p99 e headroom. Elas incluem confiança, quantidade de amostras, indicação HPA/KEDA e ressalvas sobre startup, sazonalidade, sidecars, caches, memória nativa e throttling. São propostas para validação, nunca mudanças aplicadas.

## Artefatos e proteção de dados

- `comprehensive-assessment.json`: checks, cobertura, tecnologias e capacidade;
- `nodes.json`, `pods.json`, `workloads.json`, `namespaces.json`, `pvcs.json`: snapshots com status preservado e valores arbitrários de `env` redatados;
- `events.json`: classificação e timestamps preservados, sem mensagens livres ou UIDs;
- `application-manifests-sanitized.json`: manifests de aplicação sem status/managed fields, valores arbitrários de `env`, dados de Secret ou valores de ConfigMap;
- `api-resources.json`: APIs listáveis descobertas;
- `secrets-metadata.json`: somente metadados, tipo e nomes das chaves;
- `prometheus-telemetry.json`: séries agregadas e estatísticas;
- `discovery/`: evidências das 49 seções.

Valores de tuning explicitamente permitidos (`JAVA_TOOL_OPTIONS`, `JAVA_OPTS`, opções .NET e equivalentes) podem aparecer na evidência para análise. Tokens, senhas e demais variáveis permanecem redatados.
