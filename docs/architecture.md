# Arquitetura

## Limites do ambiente

O cluster não executa dentro da WSL. VMware hospeda três VMs AlmaLinux na rede
NAT `vmnet8`; WSL, PowerShell, VS Code, PuTTY ou MobaXterm podem atuar como
clientes SSH.

```text
Browser / terminal
        |
        +-- SSH (192.168.109.151) --> k8s-master --> Kubernetes API
        |
        +-- HTTPS :31882 ----------> Istio ingressgateway
                                            |
                   +------------------------+---------------------+
                   |                        |                     |
             nginx-lab              banking services       postgres-api
                                    |          |
                              PostgreSQL   Prometheus metrics

GitHub main --> Argo CD platform-root --> platform and workload Applications
```

O control plane é único. Portanto, o laboratório tolera a indisponibilidade de
um worker, mas não do `k8s-master`.

## Plataforma

| Componente | Versão/referência | Finalidade |
|---|---:|---|
| Kubernetes | 1.35 | Orquestração |
| Flannel | 0.28.4 | CNI/pod network |
| Argo CD | 3.4.2 | Reconciliação GitOps |
| Istio | 1.30.1 | Gateway, roteamento e mTLS |
| KEDA | 2.16.1 | Escala orientada por eventos |
| Kyverno | 3.8.2 | Políticas de admissão em modo Audit |
| Metrics Server | 3.13.1 | Métricas para HPA e `kubectl top` |
| kube-prometheus-stack | 61.0.0 | Prometheus, Grafana e alertas |
| Loki stack | 2.10.3 | Agregação de logs |
| Sealed Secrets | 0.37.0 | Segredos criptografados no Git |
| VPA | manifests versionados | Recomendações verticais |

As versões exatas são fixadas nos manifests. A tabela é um resumo e deve ser
atualizada junto com qualquer upgrade.

## Aplicações Argo CD

`platform-root` implementa o padrão App of Apps. Ele cria as aplicações de
plataforma e workloads:

- plataforma: Istio, KEDA, Kyverno, Metrics Server, monitoring, logging,
  Sealed Secrets, VPA e governance;
- dados: PostgreSQL e RabbitMQ;
- workloads: banking, nginx-lab, postgres-api, cpu-producer e cpu-worker.

Todas usam sincronização automática, prune e self-heal conforme definido em
`kubernetes/argocd`. Recursos mutados por admission controllers possuem regras
de comparação específicas para evitar drift falso.

## Aplicação bancária

`account-service-java` é responsável por contas e saldos. O
`transaction-service-dotnet` registra e orquestra transferências. PostgreSQL
mantém o estado; a UUID fornecida em `Idempotency-Key` impede movimentação
duplicada em retries.

Prometheus coleta métricas dos dois runtimes e o Grafana recebe dashboards
provisionados pelo próprio GitOps. Consulte
[runtime-observability.md](runtime-observability.md).

## Armazenamento e disponibilidade

Volumes usam `local-path`, portanto permanecem vinculados ao disco de uma VM.
PostgreSQL, RabbitMQ e Loki não possuem alta disponibilidade de storage. PDB,
HPA, KEDA e VPA melhoram comportamento operacional, mas não transformam o
laboratório em uma plataforma de produção.

