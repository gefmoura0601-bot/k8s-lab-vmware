# Runbook operacional

## Verificação diária

```bash
kubectl get nodes -o wide
kubectl get pods -A
kubectl get applications -n argocd
kubectl top nodes
kubectl get events -A --sort-by=.lastTimestamp | tail -n 40
```

Condições esperadas:

- três nós `Ready`;
- nenhum pod em `CrashLoopBackOff`, `ImagePullBackOff` ou `Pending` prolongado;
- aplicações Argo CD em `Synced/Healthy`;
- ausência de pressão de memória, disco ou PID nos nós.

## Inspeção de workload

```bash
kubectl -n <namespace> get deploy,statefulset,pod,svc
kubectl -n <namespace> describe pod <pod>
kubectl -n <namespace> logs <pod> --all-containers --tail=200
kubectl -n <namespace> logs <pod> -c <container> --previous
kubectl -n <namespace> get events --sort-by=.lastTimestamp
```

Em workloads com Istio, diferencie logs da aplicação e do `istio-proxy`.

## Capacidade

```bash
kubectl top nodes
kubectl top pods -A --containers
kubectl describe node k8s-worker-02
kubectl get hpa -A
kubectl get vpa -A
kubectl get scaledobject -A
```

O `k8s-master` e o `k8s-worker-02` possuem 6 GiB. A distribuição mantém 16 GiB
no total e reserva mais memória ao control plane, que concentra API server,
etcd e os watches dos operadores. Trate requests próximos de 80% da memória
alocável de um nó como sinal para rebalancear ou ampliar.

Para gerar um inventário reproduzível de memória, execute no `k8s-master`:

```bash
cd /workspace
bash scripts/validation/report-memory-capacity.sh
```

No Grafana, o dashboard `Kubernetes / Memory Health` apresenta disponibilidade
por nó, `MemoryPressure`, OOMs recentes, maiores consumidores, requests e uso em
relação aos limites.

## Reiniciar com segurança

Para um Deployment:

```bash
kubectl -n <namespace> rollout restart deployment/<nome>
kubectl -n <namespace> rollout status deployment/<nome> --timeout=5m
```

Para manutenção de worker:

```bash
kubectl drain <worker> --ignore-daemonsets --delete-emptydir-data
# reinicie/repare a VM
kubectl uncordon <worker>
```

Não drene o control plane sem entender o impacto do cluster single-master.

## Logs e métricas

Use Grafana para visão agregada e `kubectl logs` para confirmação local. Os
dashboards de runtime e a coleta sob demanda estão em
[runtime-observability.md](runtime-observability.md).

## Certificado do endpoint

O certificado de `nginx.lab.local` é autoassinado. `curl -k` é aceitável apenas
no laboratório. Para evitar alertas no browser, importe a CA do laboratório no
trust store do cliente; não desative validação TLS globalmente.

## Atualizações

Atualize uma camada por PR, preservando referências fixas. Ordem recomendada:

1. Vagrant/AlmaLinux e containerd;
2. Kubernetes, respeitando skew suportado;
3. CNI;
4. Argo CD;
5. Istio e admission controllers;
6. observabilidade;
7. workloads.

Registre versões anteriores, plano de rollback e evidências de smoke test.

## Laboratório de pressão de memória

O namespace `memory-lab` contém um worker isolado, escalado pelo KEDA entre zero
e duas réplicas. O teste faz alocação anônima com limit de 128 MiB,
provoca um OOMKilled controlado e valida recuperação, drenagem da fila e retorno
a zero:

```bash
cd /workspace
bash scripts/validation/validate-memory-keda-e2e.sh
```

Execute somente em ambiente de laboratório. O teste confirma que nenhum nó entra
em `MemoryPressure` e não interfere nas filas usadas pelo pipeline de CPU.

O workflow manual `Validate Memory Lab` executa o mesmo cenário no runner do
master e preserva o relatório como artefato por 14 dias. Para validar também a
perda dos dois workers, um por vez, execute `Validate Memory Node Resilience` e
digite `FAIL-BOTH-WORKERS`. A matriz usa `max-parallel: 1`: o segundo nó só é
testado após a recuperação completa do primeiro.

O guia com a explicação de cada parâmetro, etapa e critério de aceite está em
[lab-completo-passo-a-passo.md](lab-completo-passo-a-passo.md).
## Laboratório de memória insuficiente no scheduler

O workflow manual `Validate Memory Unschedulable` exige a confirmação
`TEST-INSUFFICIENT-MEMORY`. Ele cria temporariamente um namespace isolado e um
pod com request de 8 GiB, restrito aos workers. Como nenhum worker oferece essa
capacidade, o pod permanece `Pending` e o scheduler emite `FailedScheduling`
com `Insufficient memory`.

O request influencia apenas a decisão do scheduler: como o container nunca
inicia, não há alocação real de 8 GiB. O teste mantém o pod por 105 segundos para
Prometheus e alertas, confirma que os nós não entraram em `MemoryPressure` e
remove o namespace automaticamente por meio de um `trap`.
