# Blue-Green Readiness e Migration Gate

O assessment avalia uma migração blue-green de forma provider-neutral e read-only. Ele não altera workloads, não troca DNS, não muda weights e não executa cutover. `GO` significa somente que os gates automatizados e as evidências manuais obrigatórias disponíveis foram aprovados.

## O que é validado

| Domínio | Evidência |
|---|---|
| Workloads | paridade de Deployment, StatefulSet, DaemonSet, Rollout, Job e CronJob; image, replicas e ServiceAccount |
| Configuration | referências a ConfigMap, Secret, imagePullSecret e TLS; existência de nomes e keys quando o opt-in está habilitado |
| Traffic | Service, EndpointSlice, Ingress, Gateway API, ReferenceGrant, Istio Gateway/VirtualService/DestinationRule e OpenShift Route |
| Resilience | NetworkPolicy, PodDisruptionBudget, HPA e KEDA |
| Compatibility | Schema & Semantic Validation, API compatibility, lifecycle e version skew no target |
| State & Data | PVC, StorageClass, VolumeSnapshot, Velero backup/restore, replicação, compatibilidade de schema, singleton jobs e rollback |
| Capacity | Node Health e headroom do target |
| Security | postura CIS do target como sinal informativo, sem certificar compliance |
| Observability | disponibilidade do Prometheus como sinal informativo |
| Cutover | probes HTTP/HTTPS explícitos, DNS, load balancer e rollback |

Ausência de evidência obrigatória produz `UNKNOWN`; nunca produz `GO`. Falha comprovada produz `NO_GO`.

Em uma coleta limitada por `--namespace`, dependências em outro namespace não são tratadas como ausentes. Por exemplo, um `VirtualService` em `banking` que referencia um Istio `Gateway` em `ingress-system` fica `UNKNOWN` até que uma coleta cluster-wide ou outra evidência autorizada cubra o namespace externo. Um objeto comprovadamente ausente dentro do escopo coletado continua `FAIL`.

## Coleta segura de ConfigMap e Secret metadata

Por padrão, ConfigMaps e Secrets não são consultados. O opt-in exige `--namespace` explícito e bloqueia coleta cluster-wide desses objetos. Para validar apenas nomes, types e keys referenciadas em um namespace:

```bash
bin/kubernetes-assessment collect \
  --phase before \
  --change-id blue \
  --namespace banking-blue \
  --configmap-metadata \
  --secret-metadata \
  --no-prometheus
```

O processo não persiste `data`, `stringData`, `binaryData`, values ou annotations. Porém, a Kubernetes API entrega o objeto completo para uma identidade com `get/list`; portanto, use uma identidade temporária, namespace restrito e somente os RoleBindings necessários de [`../deploy/rbac-configuration-metadata-namespaced.yaml`](../deploy/rbac-configuration-metadata-namespaced.yaml). O CLI, o dashboard, o collector e o preflight rejeitam o opt-in sem namespace; o preflight também falha antes da coleta quando o RBAC correspondente está ausente.

## Probes de cutover

Uma ou mais URLs podem ser informadas explicitamente:

```bash
bin/kubernetes-assessment blue-green-gate \
  --collection eks-COLETA-GREEN \
  --probe-url https://green.example.test/health
```

Os probes usam somente `GET`, não seguem redirects, não persistem response body e rejeitam credenciais, query string, fragmentos, metadata endpoints, endereços link-local, multicast e unspecified. `UNKNOWN` e `NO_GO` retornam exit code `1`; somente `GO` retorna `0`.

URLs rejeitadas são registradas sem userinfo, query ou fragment. O Migration Gate também não copia o `context` Kubernetes para seus outputs, evitando propagar ARNs ou identificadores de conta que possam existir no kubeconfig.

## Evidência manual

DNS, load balancer, replicação, restore, compatibilidade de schema e rollback não podem ser inferidos com segurança apenas pela Kubernetes API. Copie [`migration-evidence.example.json`](migration-evidence.example.json) para `migration-evidence.json` no diretório da coleta target, substitua as referências e registre apenas evidência não sensível. O arquivo possui JSON Schema e campos desconhecidos são rejeitados.

## Comparar source e target

O Migration Gate permite clusters e namespaces diferentes. A relação precisa ser explícita por mapping:

```bash
bin/kubernetes-assessment migration-gate \
  --source eks-COLETA-BLUE \
  --target eks-COLETA-GREEN \
  --mapping docs/migration-mapping.example.json
```

`namespaceMap` mapeia namespaces. `resourceMap` usa a identidade `Kind/namespace/name`; recursos cluster-scoped usam `-` como namespace. `allowedDifferences` aceita diferenças planejadas, com `image` e `replicas` como padrão. O gate permite identidades de cluster diferentes, mas exige que ambas as coletas estejam `COMPLETED`.

Saídas no diretório target:

- `migration-comparison.json`;
- `migration-comparison.junit.xml`;
- `migration-comparison.sarif.json`;
- `migration-comparison.md`.

## Artefatos por coleta

- `configuration-metadata.json`: opt-in e sem values;
- `configuration-references.json`: grafo de dependências e resolução;
- `traffic-paths.json`: rotas, parents, backends, endpoints, TLS e weights;
- `state-data-readiness.json`: storage, dados e gates manuais;
- `migration-probes.json`: status/latência sem response body;
- `blue-green-readiness.json`: decisão consolidada da coleta.

Todos os artefatos possuem JSON Schema. No dashboard, use **Migração → Blue-Green Readiness** para revisar evidências, atualizar probes e executar a comparação source → target.

## Limitações explícitas

- ConfigMap e Secret values não são comparados.
- DNS autoritativo, load balancer externo e consistência de dados exigem evidência manual ou integração futura específica.
- Um probe HTTP saudável não comprova consistência transacional.
- A descoberta de stateful workloads combina StatefulSet, PVC e server image. Workloads claramente identificados como client, test, fixture, migration, backup ou exporter não são classificados como servidor apenas por reutilizarem uma imagem de banco.
- `GO` não substitui change approval, freeze, comunicação, monitoramento durante o cutover ou teste de rollback.
- EKS, AKS e GKE continuam fora da qualificação deste ciclo; as regras aqui usam somente evidência Kubernetes genérica.
