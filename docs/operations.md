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

`k8s-worker-02` possui 8 GiB porque concentra cargas que excediam a capacidade
original de 4 GiB. Trate requests próximos de 80% da memória alocável de um nó
como sinal para rebalancear ou ampliar.

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

