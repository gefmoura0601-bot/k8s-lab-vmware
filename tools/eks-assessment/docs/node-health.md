# Node Health

`Node Health` avalia nodes Kubernetes de forma provider-neutral. O mesmo contrato é usado em on-premises, Amazon EKS, Azure AKS, Google GKE e Kubernetes gerenciado por outro provider. A coleta é read-only e não depende de SSH, `/etc/kubernetes`, filesystem do node ou acesso ao control plane.

## Evidence Sources

- `KubernetesAPI`: capacity, allocatable, Pods agendados, requests, `Ready`, condições de pressão, sistema operacional e container runtime;
- `MetricsAPI`: uso pontual total do node e uso por container/Pod;
- `EVIDENCE_UNAVAILABLE`: Metrics API ausente, RBAC insuficiente ou série incompleta.

Uma avaliação sem uso total do node não recebe `PASS`. Nesse caso, condições e requests continuam visíveis, mas o estado mínimo é `PARTIAL`.

## Decomposição

O uso observado de CPU e memória é classificado em categorias mutuamente exclusivas:

1. `DaemonSets`: Pods cujo owner é um DaemonSet, inclusive em namespaces de sistema;
2. `Kubernetes/System Pods`: demais Pods de namespaces de plataforma conhecidos;
3. `Application workloads`: Pods restantes;
4. `Node overhead / não atribuído`: uso total do node menos os Pod metrics observados;
5. `Headroom`: allocatable menos o uso total do node.

`Node overhead / não atribuído` pode incluir sistema operacional, kernel, kubelet, container runtime/containerd e Pods sem métrica. A Metrics API não fornece atribuição por processo; portanto, o assessment não apresenta esses componentes como valores independentes nem presume precisão que a evidência não oferece.

`Reserva do node` é calculada como `capacity - allocatable`. Ela representa espaço reservado pelo modelo do node e thresholds de eviction; não é consumo real. Requests representam reserva de scheduling, também não consumo.

## Estado de saúde

O estado usa a maior severidade comprovada:

- `CRIT`: node não `Ready`, condição de pressão, CPU >= 95%, memória >= 90%, requests >= 100% ou densidade de Pods >= 95%;
- `WARN`: CPU >= 85%, memória >= 80%, requests >= 85% ou densidade de Pods >= 80%;
- `PARTIAL`: node sem uso total da Metrics API ou com Pod metrics incompletos, sem sinal mais grave;
- `PASS`: node `Ready`, sem pressão, dentro dos thresholds e com uso total disponível;
- `EVIDENCE_UNAVAILABLE`: nenhum node pôde ser avaliado.

Os thresholds são referências operacionais iniciais, não SLOs universais. Devem ser calibrados com histórico, burst, workloads críticos, autoscaling, eviction thresholds e políticas do provider.

## RBAC mínimo

Para cobertura completa:

```yaml
- apiGroups: [""]
  resources: ["nodes", "pods"]
  verbs: ["get", "list"]
- apiGroups: ["metrics.k8s.io"]
  resources: ["nodes", "pods"]
  verbs: ["get", "list"]
```

O exemplo completo está em `deploy/rbac-cluster-readonly.yaml`. Uma Role namespaced pode ler Pod metrics apenas no namespace autorizado e, por desenho, não comprova a saúde completa do node.
