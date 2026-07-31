# Kubernetes VMware Lab

Laboratório Kubernetes reproduzível em VMs VMware, provisionado por Vagrant e
operado por GitOps com Argo CD. O Windows usa PowerShell, Vagrant e OpenSSH para
administrar o ambiente; o cluster e o `kubectl` executam nas VMs.

## Visão rápida

| Camada | Implementação |
|---|---|
| Virtualização | VMware Workstation + Vagrant |
| Sistema operacional | AlmaLinux 9 |
| Kubernetes | kubeadm 1.35, containerd e Calico |
| GitOps | Argo CD 3.4, padrão App of Apps |
| Rede de aplicações | Istio, mTLS e NetworkPolicy |
| Observabilidade | Prometheus, Grafana, Loki e Promtail |
| Segurança | Sealed Secrets, Kyverno, RBAC e containers restritos |
| Aplicações | Java 25, .NET 10 e serviços Go |

### Topologia

| Nó | IP de gerenciamento | Função | Recursos |
|---|---:|---|---:|
| `k8s-master` | `192.168.109.151` | control plane | 4 vCPU / 4 GiB |
| `k8s-worker-01` | `192.168.109.153` | worker | 4 vCPU / 4 GiB |
| `k8s-worker-02` | `192.168.109.155` | worker | 4 vCPU / 8 GiB |

O endpoint público do laboratório é
`https://nginx.lab.local:31882`, com certificado autoassinado.

## Comece aqui

- [Índice completo da documentação](docs/README.md)
- [Preparação e provisionamento](docs/getting-started.md)
- [Acesso ao cluster, Argo CD, Grafana e RabbitMQ](docs/access.md)
- [Arquitetura e catálogo de componentes](docs/architecture.md)
- [Operação GitOps e CI/CD](docs/gitops-cicd.md)
- [Runbook operacional](docs/operations.md)
- [Segurança e gestão de segredos](docs/security.md)
- [Backup, reconstrução e recuperação](docs/disaster-recovery.md)
- [Solução de problemas](docs/troubleshooting.md)
- [Observabilidade de runtime Java e .NET](docs/runtime-observability.md)
- [Aplicação bancária de exemplo](app/README-banking.md)

## Estado saudável esperado

```text
kubectl get nodes
# três nós em Ready

kubectl get applications -n argocd
# todas as aplicações em Synced e Healthy
```

Mudanças permanentes devem ser feitas neste repositório por Pull Request. Evite
editar recursos administrados pelo Argo CD diretamente no cluster: a
reconciliação substituirá essas alterações.
