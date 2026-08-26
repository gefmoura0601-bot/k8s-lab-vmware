# Telemetria Prometheus opcional

`tools/eks-assessment/src/prometheus_telemetry.py` é um coletor Python 3.10+ adaptativo, somente leitura e sem dependências externas. A URL do Prometheus é sempre explícita: não há descoberta automática de endpoint, credenciais no código ou mutações no cluster.

## Execução no master

Para analisar todos os Deployments de um snapshot:

```bash
python3.11 /workspace/tools/eks-assessment/src/prometheus_telemetry.py \
  --url http://prometheus.example:9090 \
  --window 7d \
  --workloads-file /workspace/assessment/<coleta>/workloads.json \
  --workers 3
```

Para alvos pontuais:

```bash
python3.11 /workspace/tools/eks-assessment/src/prometheus_telemetry.py \
  --url http://prometheus.example:9090 \
  --window 1d \
  --workload payments/api \
  --workload checkout/web
```

A forma recomendada é abrir o menu com `bash /workspace/tools/eks-assessment/bin/eks-assessment.sh`, escolher a web e informar a URL no formulário de **Coletar agora** ou **Novo baseline**.

## Descoberta automática

O coletor não pressupõe nomes de aplicações, namespaces ou labels do lab. Ele:

1. valida `/api/v1/status/runtimeinfo`;
2. lê o catálogo por `/api/v1/label/__name__/values`, usando `/api/v1/metadata` como fallback;
3. classifica famílias por semântica e nomes de métricas;
4. consulta `/api/v1/series` para descobrir os labels reais de namespace, pod, workload e heap;
5. correlaciona os valores desses labels com cada Deployment;
6. consulta somente `/api/v1/query_range` para calcular média, pico, p50, p90, p95 e p99;

Labels como `namespace`/`pod`, `k8s_namespace_name`/`k8s_pod_name` ou equivalentes são aceitos quando os valores identificam o workload. A ausência de uma série vira `NO_DATA`/`N/A`, nunca conformidade.

## Métricas

A coleta básica procura famílias compatíveis de:

- CPU, memória total, working set, throttling, reinícios, OOM e rede;
- JVM: identidade/versão, heap usado/máximo, GC, threads, alocação e memória nativa;
- .NET: identidade/versão, managed heap/máximo, GC, thread pool, alocação, exceções e working set;

O runtime é inferido por duas fontes independentes: imagem/comando/variáveis do manifest e séries reais do Prometheus. A saída registra a métrica-fonte e os labels usados para que a correlação seja auditável.

Janelas disponíveis: `1d`, `3d`, `7d`, `14d` e `30d`. Estados: `DISABLED`, `UNAVAILABLE`, `PARTIAL`, `AVAILABLE` e `NO_DATA`.

## Segurança e tuning

Somente HTTP `GET` é usado. Esquemas diferentes de HTTP/HTTPS, query string, fragmento ou credenciais embutidas na URL são rejeitados. Respostas são limitadas a 64 MiB e chamadas transitórias usam retry/backoff limitado.

Opções conhecidas de JVM e famílias seguras de runtime .NET (`DOTNET_`, `COMPlus_`, `CORECLR_`, `MONO_`, `ASPNETCORE_`) são associadas ao workload. Nomes ou valores com padrão de senha, token, secret, credential, private key, API key ou connection string são redigidos. Variáveis arbitrárias não são exportadas.

A aba **Prometheus** apresenta percentuais de request/limit e heap, além de versão, GC, threads, alocação, working set, exceções, métricas-fonte e opções aplicadas. As propostas de requests/limits são recomendações para validação; nada é aplicado automaticamente.

## Testes

```bash
cd /workspace/tools/eks-assessment
python3.11 -m unittest -v test_assessment_cancellation.py test_assessment_supply_cost.py test_prometheus_telemetry.py test_eks_assessment.py
```
