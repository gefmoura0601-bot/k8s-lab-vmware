# Entrega progressiva com Argo Rollouts

## Objetivo

Este laboratório troca o `Deployment` da `postgres-api` por um `Rollout`. A
mudança permite liberar uma revisão aos poucos, medir seu comportamento real e
interromper automaticamente uma versão que produza erros, sem depender da AWS.

## Componentes

| Componente | Função |
|---|---|
| Argo CD | instala o chart e reconcilia os manifests armazenados no Git |
| Argo Rollouts | cria revisões, executa as etapas canary e promove ou aborta |
| Istio | divide o tráfego entre as revisões estável e canary |
| Prometheus | fornece a taxa de sucesso HTTP usada pela análise |
| HPA | mantém a quantidade configurada de réplicas do `Rollout` |
| VPA | recomenda CPU e memória, sem alterar pods automaticamente |

O chart `argo-rollouts` é declarado em
`kubernetes/argocd/apps/argo-rollouts.yaml`, instalado no namespace
`argo-rollouts` e configurado com uma única réplica para economizar memória. O
dashboard do componente usa `ClusterIP`, portanto não fica exposto
permanentemente fora do cluster.

## Estratégia da postgres-api

O arquivo `kubernetes/apps/postgres-api/rollout.yaml` mantém duas réplicas e
usa `maxSurge: 1` e `maxUnavailable: 0`. Assim, somente um pod adicional é
criado durante a troca e os pods saudáveis continuam atendendo. Os limites do
aplicativo e do sidecar Istio foram reduzidos para respeitar a memória do lab.

A sequência é:

1. enviar 10% do tráfego para a revisão nova;
2. aguardar 30 segundos e analisar a taxa de sucesso;
3. repetir com 25% e 50%;
4. promover para 100% somente depois das três análises.

O `VirtualService` contém a rota nomeada `primary`. O controller muda os pesos
dessa rota entre os Services `postgres-api-stable` e `postgres-api-canary`. Os
seletores com o hash de cada revisão também são mantidos pelo controller. O
Argo CD ignora apenas esses campos dinâmicos para não desfazer a progressão.
O Service original `postgres-api` permanece como endereço interno estável e
como alvo de métricas.

## Análise e rollback

`postgres-api-success-rate` consulta `istio_requests_total` no Prometheus duas
vezes, a cada 15 segundos. A revisão precisa atingir pelo menos 99% de respostas
sem erro 5xx. Uma medição abaixo do limite reprova a `AnalysisRun`, deixa o
Rollout em `Degraded` e devolve todo o tráfego à revisão estável.

`postgres-api-forced-failure` retorna uma falha determinística e existe apenas
para testar o mecanismo. Ele não aparece nas etapas da entrega normal.

O HPA e o VPA apontam para `argoproj.io/v1alpha1`, tipo `Rollout`, em vez de
`apps/v1`, tipo `Deployment`. O HPA continua controlando réplicas e o VPA
permanece em modo `Off`, apenas recomendando recursos.

## Como acompanhar

No control plane:

```bash
kubectl -n apps get rollout postgres-api -w
kubectl -n apps get analysisrun -w
kubectl -n apps get virtualservice postgres-api \
  -o jsonpath='{.spec.http[0].route[*].weight}{"\n"}'
kubectl -n apps get pods -l app=postgres-api -L rollouts-pod-template-hash
```

Estados importantes:

- `Progressing`: uma revisão está percorrendo as etapas;
- `Paused`: o controller está cumprindo a espera configurada;
- `Healthy`: a revisão terminou e foi promovida;
- `Degraded`: uma análise falhou e a promoção foi interrompida.

No Grafana, abra `Postgres API Overview` para correlacionar consumo da API. Para
inspecionar diretamente o dashboard do Argo Rollouts, crie um túnel a partir do
Windows:

```powershell
ssh -L 3100:127.0.0.1:3100 vagrant@IP_DO_MASTER
```

Na sessão SSH do master:

```bash
kubectl -n argo-rollouts port-forward service/argo-rollouts-dashboard 3100:3100
```

Depois acesse `http://localhost:3100` no Windows.

## Teste reproduzível de rollback

Execute somente quando o Rollout estiver `Healthy`:

```bash
cd /workspace
bash scripts/validation/validate-postgres-api-rollout.sh
```

O script salva o hash estável, cria uma revisão com análise forçada a falhar,
espera `Degraded`, confirma uma `AnalysisRun` reprovada e valida os pesos
`stable=100` e `canary=0`. Um `trap` reaplica o manifesto normal mesmo se a
validação falhar e aguarda o retorno a `Healthy`.

No GitHub, o workflow manual `Validate Argo Rollouts rollback` executa o mesmo
procedimento no runner self-hosted `master`. Informe a confirmação
`TEST-POSTGRES-API-ROLLBACK`. Ao final, os manifests do Rollout, AnalysisRuns,
VirtualService e pods ficam disponíveis como artefato por 14 dias.

## Diagnóstico

```bash
kubectl -n argo-rollouts logs deployment/argo-rollouts --tail=200
kubectl -n apps describe rollout postgres-api
kubectl -n apps describe analysisrun NOME_DA_ANALISE
kubectl -n apps get endpointslices -l kubernetes.io/service-name=postgres-api-canary
```

Se não houver tráfego canary, confira o nome `primary`, os hosts dos dois
Services e a injeção do sidecar. Se a análise retornar ausência de dados, gere
requisições durante a janela ou consulte a expressão diretamente no
Prometheus. Nunca edite pesos e hashes no cluster como correção permanente;
altere o Git e deixe os controllers reconciliarem.
