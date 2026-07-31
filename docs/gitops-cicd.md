# GitOps e CI/CD

## Regra de mudança

A branch `main` é a fonte de verdade. O fluxo normal é:

1. criar branch curta;
2. alterar código ou manifestos;
3. executar validações locais possíveis;
4. abrir Pull Request;
5. aguardar `Validate IaC`;
6. revisar e fazer squash merge;
7. acompanhar o Argo CD até `Synced/Healthy`.

Não use `kubectl edit` como correção permanente. Uma alteração emergencial deve
ser reproduzida no Git imediatamente.

## Validação

```bash
make validate-local
```

Esse target exige Go, ShellCheck e kubectl. O workflow `Validate IaC` também
valida:

- sintaxe Ruby do Vagrantfile e scripts com ShellCheck;
- YAML e render de todas as árvores Kustomize;
- referências imutáveis e ausência de artefatos;
- `gofmt`, `go vet` e testes com race detector;
- build/test de Java 25 e .NET 10.

Smoke tests que dependem do cluster:

```bash
make validate-platform-smoke
make validate-cpu-pipeline-e2e
make validate-postgres-api-mesh
```

## Pipelines de imagem

Alterações nos serviços disparam workflows específicos. Eles:

1. testam e constroem a imagem;
2. publicam no GHCR;
3. atualizam o manifest para uma tag baseada no SHA;
4. abrem um PR GitOps de automação.

O merge desse segundo PR é o ato de promoção para o laboratório. A exceção
histórica de tags `latest` nos workflows não deve aparecer nos manifests
implantados.

## Reconciliação

Verifique o root e os filhos:

```bash
kubectl -n argocd get applications
kubectl -n argocd get application platform-root \
  -o jsonpath='{.status.sync.status}{" "}{.status.health.status}{"\n"}'
```

Para pedir nova comparação:

```bash
kubectl -n argocd annotate application <APP> \
  argocd.argoproj.io/refresh=hard --overwrite
```

O workflow manual `Reconcile Argo CD` roda em um runner self-hosted rotulado
`master`, protegido pelo environment GitHub `lab`.

## Diagnóstico de OutOfSync

Antes de sincronizar à força, identifique o recurso:

```bash
kubectl -n argocd get application <APP> -o json | \
  jq -r '.status.resources[] | select(.status != "Synced") |
  [.kind,.namespace,.name,.status,.health.status] | @tsv'
```

Confirme se é mudança real, mutação legítima de controller ou configuração de
comparação. Regras de `ignoreDifferences` e `ServerSideDiff` devem ser mínimas,
justificadas e versionadas.

