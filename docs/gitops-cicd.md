# GitOps e CI/CD

## Regra de mudança

A branch `main` é a fonte de verdade. O fluxo normal é:

1. criar branch curta;
2. alterar código ou manifestos;
3. abrir Pull Request para disparar as validações;
4. aguardar `Validate IaC`;
5. revisar e fazer squash merge;
6. acompanhar o Argo CD até `Synced/Healthy`.

Não use `kubectl edit` como correção permanente. Uma alteração emergencial deve
ser reproduzida no Git imediatamente.

## Validação

O workflow `Validate IaC` executa no GitHub Actions em todo Pull Request e push
na `main`. Ele valida:

- sintaxe Ruby do Vagrantfile e scripts com ShellCheck;
- YAML e render de todas as árvores Kustomize;
- referências imutáveis e ausência de artefatos;
- `gofmt`, `go vet` e testes com race detector;
- build/test de Java 25 e .NET 10.

Os testes que dependem do cluster ficam no workflow manual `Validate Cluster`,
executado pelo runner self-hosted rotulado `master` e protegido pelo environment
`lab`. O smoke inclui a validação do mesh da postgres-api. Ao disparar o
workflow, habilite `run_cpu_e2e` somente quando quiser recriar o pod producer e
observar carga, escala e drenagem da fila por cerca de três minutos.

## Pipelines de imagem

Alterações nos serviços disparam workflows específicos. Eles:

1. testam e constroem a imagem;
2. publicam no GHCR;
3. atualizam o manifest para uma tag baseada no SHA;
4. abrem um PR GitOps de automação.

O merge desse segundo PR é o ato de promoção para o laboratório. Workflows e
manifests publicam e consomem somente tags imutáveis baseadas no SHA.

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
## Runner self-hosted

O provisioner `actions-runner` instala o runner fixado e verifica seu SHA-256.
O token de registro nunca é versionado. Em uma reconstrução do master, gere um
token temporário e execute no PowerShell:

```powershell
$env:ACTIONS_RUNNER_TOKEN = '<token temporário>'
Set-Location C:\Labs\k8s-vmware\iac\vagrant
vagrant provision k8s-master --provision-with actions-runner
Remove-Item Env:ACTIONS_RUNNER_TOKEN
```

Sem a variável, o provisioner instala os binários, mas não registra um runner
novo. Uma instalação já registrada apenas tem o serviço validado e iniciado.

## Teste de resiliência do worker

O workflow manual `Validate Memory Node Resilience` exige a confirmação
`FAIL-WORKER-01`. Ele interrompe kubelet e containerd no `k8s-worker-01`, força
o reagendamento do memory-worker no segundo worker e sempre recupera e
descordona o nó por meio de um trap. Execute somente no environment `lab`.