# Rastreamento distribuído com OpenTelemetry e Tempo

## O que foi construído

O lab passa a reunir os três sinais principais de observabilidade: Prometheus
para métricas, Loki para logs e Tempo para traces. A `postgres-api` cria um span
servidor para cada requisição HTTP e um span filho para a operação PostgreSQL.
Isso permite enxergar, em uma única linha do tempo, onde uma chamada gastou
tempo e em qual etapa ocorreu um erro.

O fluxo é:

```text
cliente -> Istio -> postgres-api -> PostgreSQL
                       |
                       v OTLP/gRPC :4317
              OpenTelemetry Collector -> Tempo -> Grafana
```

## Por que esta topologia

Tempo roda como um único processo, adequado a desenvolvimento e laboratório.
Ele usa um PVC local de 2 GiB e retém blocos por seis horas. Não há replicação:
se o nó ou volume for perdido, os traces são descartáveis. Produção exige
storage de objetos, alta disponibilidade, autenticação e dimensionamento.

O Collector funciona como gateway. A aplicação não conhece detalhes do Tempo;
envia OTLP ao Collector, que aplica proteção de memória e batching antes de
exportar. Assim o backend pode ser trocado sem recompilar serviços.

Limites escolhidos por causa da memória do lab:

| Componente | Request | Limit |
|---|---:|---:|
| Tempo | 192 MiB | 384 MiB |
| OpenTelemetry Collector | 64 MiB | 128 MiB |

O `memory_limiter` começa a recusar carga antes de ultrapassar 96 MiB e o
processor `batch` agrupa até 256 spans por envio. A amostragem é de 100% para
facilitar o aprendizado; em produção, use amostragem probabilística ou baseada
em cauda.

## Instrumentação da aplicação

`setupTracing` cria um `TracerProvider`, exportador OTLP gRPC e propagadores
W3C Trace Context/Baggage. O recurso identifica `service.name=postgres-api`, a
versão da release e `deployment.environment=lab`. `otelhttp` extrai ou cria o
contexto da requisição, enquanto os handlers de usuários criam os spans
`postgresql.users.select` e `postgresql.users.insert`.

As variáveis no Rollout são:

- `OTEL_EXPORTER_OTLP_ENDPOINT`: Service interno do Collector;
- `OTEL_SERVICE_NAME`: nome pesquisável no Tempo;
- `OTEL_RESOURCE_ATTRIBUTES`: ambiente do recurso.

Uma NetworkPolicy separada permite somente TCP/4317 da `postgres-api` para pods
`otel-collector` no namespace `tracing`.

## Acesso no Grafana

O ConfigMap `grafana-tempo-datasource` é descoberto pelo sidecar do Grafana.
Abra Grafana conforme [access.md](access.md), vá a **Explore**, escolha
**Tempo**, selecione **Search** e filtre `service.name = postgres-api`.
Ao abrir um trace, o Node Graph mostra as relações e a integração
`tracesToLogsV2` permite saltar para logs próximos ao span.

## Validação ponta a ponta

```bash
cd /workspace
bash scripts/validation/validate-tracing-e2e.sh
```

O script aguarda Tempo e Collector, faz dez consultas a `/users`, pesquisa um
trace pelo serviço e confirma que a resposta contém tanto `postgres-api`
quanto `postgresql.users.select`. O workflow manual `Validate distributed
tracing` executa o mesmo teste com a confirmação `TEST-TRACING-E2E` e guarda
logs e consumo dos nós por 14 dias.

## Diagnóstico

```bash
kubectl -n tracing get pods,pvc
kubectl -n tracing logs deployment/otel-collector --tail=200
kubectl -n tracing logs deployment/tempo --tail=200
kubectl -n apps logs -l app=postgres-api -c postgres-api --tail=100
```

Sem traces, valide DNS e TCP/4317, a NetworkPolicy, as variáveis `OTEL_*` e os
logs do Collector. Se houver `OOMKilled`, reduza a amostragem antes de aumentar
limites. O PVC local significa que o pod Tempo precisa permanecer no nó ao qual
o volume foi vinculado.
