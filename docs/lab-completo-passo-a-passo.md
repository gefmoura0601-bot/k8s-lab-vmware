# Laboratório Kubernetes completo: construção, conceitos e validação

Este é o guia principal para reproduzir o laboratório desde um computador
Windows vazio. Ele explica o que cada camada faz, por que foi escolhida, onde
está configurada e como provar que funciona. Os demais documentos são runbooks
especializados e devem ser consultados pelos links ao longo deste guia.

> Este ambiente é educacional. Ele possui um único control plane, storage local,
> certificado autoassinado e políticas Kyverno em modo de auditoria. Esses
> limites são intencionais e não representam uma arquitetura de produção.

## 1. O que será construído

O resultado são três VMs AlmaLinux 9.7 executadas no VMware Workstation:

| VM | IP | Papel | CPU | Memória | Motivo da distribuição |
|---|---:|---|---:|---:|---|
| `k8s-master` | `192.168.109.151` | control plane e runner | 4 | 6 GiB | API, etcd e operadores consomem memória continuamente |
| `k8s-worker-01` | `192.168.109.153` | workloads | 4 | 4 GiB | nó menor para observar restrições do scheduler |
| `k8s-worker-02` | `192.168.109.155` | workloads | 4 | 6 GiB | recebe cargas maiores e substituições em falhas |

Fluxo resumido:

```text
GitHub main -> Argo CD -> recursos Kubernetes
                         |
RabbitMQ -> KEDA -> Deployments -> pods nos workers
                         |
        Prometheus <- métricas -> Grafana/alertas
        Loki       <- logs     -> Grafana
```

Conceitos essenciais:

- uma **VM** simula uma máquina física;
- um **nó** é uma VM que participa do Kubernetes;
- o **control plane** mantém o estado e decide onde executar pods;
- um **pod** é a menor unidade executável do Kubernetes;
- um **Deployment** mantém a quantidade desejada de pods stateless;
- um **StatefulSet** mantém identidade e volumes de serviços com estado;
- um **Service** oferece um endereço estável para pods variáveis;
- um **namespace** separa recursos por domínio;
- GitOps significa que o Git, não uma edição manual no cluster, é a fonte de
  verdade.

## 2. Estrutura do repositório

| Caminho | Responsabilidade |
|---|---|
| `iac/vagrant/` | VMs, rede, sistema operacional e bootstrap do Kubernetes |
| `kubernetes/argocd/` | aplicações que o Argo CD reconcilia |
| `kubernetes/platform/` | observabilidade, governance, VPA e TLS |
| `kubernetes/policies/` | políticas Kyverno |
| `kubernetes/apps/` | bancos, mensageria e workloads |
| `app/` | código-fonte Java, .NET e Go |
| `.github/workflows/` | CI, promoção GitOps e testes do cluster |
| `scripts/validation/` | testes reproduzíveis e relatórios |
| `scripts/diagnostics/` | coletas Java e .NET sob demanda |

O compartilhamento Vagrant monta a raiz do repositório como `/workspace` no
master. Por isso os scripts do cluster usam `cd /workspace`.

## 3. Pré-requisitos no Windows

Instale:

1. VMware Workstation;
2. Vagrant 2.4 ou mais recente;
3. Vagrant VMware Utility e provider VMware Desktop;
4. Git for Windows;
5. OpenSSH Client;
6. opcionalmente, GitHub CLI (`gh`) para PRs e workflows.

Reserve 12 vCPUs, 16 GiB de RAM e espaço para três discos virtuais. A rede
`vmnet8` deve usar `192.168.109.0/24`, sem ocupar os IPs `.151`, `.153` e `.155`.

Valide no PowerShell:

```powershell
vmware.exe 2>$null
vagrant --version
vagrant plugin list
git --version
ssh.exe -V
```

O lab não exige `kubectl` no Windows: ele roda dentro do master. Isso evita
divergência de versões e simplifica o kubeconfig.

## 4. Como o provisionamento funciona

O [Vagrantfile](../iac/vagrant/Vagrantfile) fixa a box AlmaLinux, recursos,
endereços e versões principais. Cada VM passa por provisionadores idempotentes:

1. `network.sh`: reaplica IP e DNS em toda inicialização;
2. `common.sh`: prepara kernel, containerd, kubelet, kubeadm e kubectl;
3. `control-plane.sh`: cria o cluster, instala Calico, storage e Argo CD;
4. `worker.sh`: usa o comando temporário de `kubeadm join`;
5. `actions-runner.sh`: instala e, quando autorizado, registra o runner GitHub.

### 4.1 Rede e sistema operacional

`common.sh` desliga swap porque o kubelet depende de contabilização previsível
de memória, carrega `overlay` e `br_netfilter`, habilita encaminhamento IPv4 e
configura cgroups do containerd com `SystemdCgroup=true`.

SELinux fica permissivo e o firewalld desativado somente para reduzir variáveis
didáticas. Em produção, ambos devem ser configurados, não removidos.

O arquivo `/etc/hosts` das VMs resolve os três nomes. No Windows, use o IP do
master ou cadastre manualmente `k8s-master`; o DNS do host não herda o arquivo
das VMs.

### 4.2 Bootstrap do Kubernetes

O master executa `kubeadm init` com:

- endpoint `k8s-master:6443`;
- API anunciada em `192.168.109.151`;
- pod CIDR `10.244.0.0/16`;
- Kubernetes da série `v1.35`.

O kubeconfig é copiado para `vagrant` e `root`. O join tem validade de duas
horas e fica temporariamente em `.cluster/join-command.sh` no diretório Vagrant
compartilhado.

### 4.3 Criar as VMs

```powershell
Set-Location C:\Labs\k8s-vmware\iac\vagrant
vagrant validate
vagrant up
vagrant status
```

Na primeira execução há downloads de box, pacotes, imagens e manifests. Ao
final, valide:

```powershell
vagrant ssh k8s-master -c "kubectl get nodes -o wide"
vagrant ssh k8s-master -c "kubectl get tigerastatus"
```

Aceite: três nós `Ready` e `tigerastatus/calico` disponível.

Se `vagrant ssh k8s-master` não resolver a máquina, execute o comando dentro de
`iac/vagrant`. O ID global é apenas recurso de diagnóstico; o nome é o fluxo
normal quando o diretório correto contém o estado `.vagrant`.

## 5. Rede de pods: Calico

Calico 3.32.1 fornece CNI, endereços dos pods e aplicação de NetworkPolicy. O
modo VXLAN encapsula tráfego entre VMs e usa o pool `10.244.0.0/16`. Typha
reduz conexões entre agentes Felix e a API, algo útil para estudar a arquitetura
operacional do CNI mesmo em um cluster pequeno.

As configurações ficam em `iac/vagrant/calico/installation.yaml`; métricas,
ServiceMonitors, dashboards e alertas ficam em
`kubernetes/platform/calico-monitoring/`.

Valide:

```bash
kubectl get tigerastatus
kubectl -n calico-system get pods -o wide
kubectl get networkpolicy -A
```

Não troque CNI em um cluster ativo. Interfaces e rotas antigas permanecem; para
uma troca segura neste lab, faça backup e reconstrua as VMs.

## 6. Storage local

O `local-path-provisioner` 0.0.32 cria volumes no disco do nó onde o pod foi
agendado. `WaitForFirstConsumer` posterga a escolha do volume até o scheduler
escolher o nó.

Isso é simples e adequado ao lab, mas não é HA: perder a VM pode significar
perder PostgreSQL, RabbitMQ ou Loki. PDB protege contra algumas interrupções
voluntárias, porém não replica dados.

```bash
kubectl get storageclass
kubectl get pv,pvc -A
```

## 7. GitOps com Argo CD

Argo CD 3.4.2 é instalado pelo bootstrap. O recurso `platform-root` implementa
**App of Apps**: ele cria aplicações-filhas de plataforma e workloads. Todas
acompanham `main` e usam `prune` e `selfHeal` onde definido.

Em termos práticos:

1. um manifest muda numa branch;
2. o PR executa validações;
3. após o merge, `main` muda;
4. Argo CD detecta a revisão;
5. cria, atualiza ou remove recursos até o cluster igualar o Git.

Nunca use `kubectl edit` como solução permanente, pois o self-heal poderá
desfazer a alteração.

O repositório privado exige uma deploy key somente leitura. Crie o Secret de
repositório conforme [getting-started.md](getting-started.md), sem versionar a
chave privada.

```bash
kubectl -n argocd get applications
kubectl -n argocd get application platform-root
```

Aceite: as 23 aplicações devem estar `Synced` e `Healthy`.

## 8. Componentes de plataforma

| Componente | Versão | O que faz | Por que existe no lab |
|---|---:|---|---|
| Istio | 1.30.1 | sidecars, mTLS, gateway e roteamento | estudar service mesh e tráfego seguro |
| KEDA | 2.16.1 | converte eventos externos em escala | escalar consumidores por fila RabbitMQ |
| Metrics Server | chart 3.13.1 | CPU/memória recentes | alimentar HPA e `kubectl top` |
| VPA | manifests fixados | recomenda requests verticais | comparar recomendação com HPA/KEDA |
| Kyverno | 3.8.2 | avalia políticas de admissão | ensinar governance sem bloquear o lab |
| Sealed Secrets | 0.37.0 | criptografa segredos para Git | manter credenciais fora de texto claro |
| Prometheus/Grafana | chart 61.0.0 | métricas, dashboards e alertas | observar capacidade e comportamento |
| Loki stack | 2.10.3 | centraliza logs com Promtail | correlacionar logs e métricas |

### 8.1 Istio

Namespaces de aplicações usam injeção do proxy onde configurado. O
`PeerAuthentication` aplica mTLS, `VirtualService` roteia requisições e
`AuthorizationPolicy` restringe chamadas. Exceções de scrape são limitadas às
portas de métricas quando o Prometheus precisa chegar à aplicação.

O ingress gateway expõe HTTPS por NodePort `31882`; o certificado é
autoassinado. Teste:

```bash
curl -sk --resolve nginx.lab.local:31882:192.168.109.151 \
  https://nginx.lab.local:31882/
```

### 8.2 KEDA, HPA e VPA

- HPA escala usando métricas de recursos;
- KEDA cria e alimenta um HPA a partir de eventos, inclusive fila RabbitMQ;
- VPA do `postgres-api` está em modo `Off`: apenas recomenda, sem reiniciar pods;
- não use HPA e VPA automático sobre o mesmo recurso de CPU/memória sem planejar
  a interação.

```bash
kubectl get hpa -A
kubectl get scaledobject -A
kubectl get vpa -A
```

### 8.3 Kyverno e governance

As políticas auditam imagens `latest`, registries não aprovados, probes,
requests/limits e labels padrão. O modo Audit registra violações sem rejeitar o
recurso, adequado para aprender e corrigir antes de migrar para Enforce.

PriorityClasses distinguem plataforma crítica, aplicação normal e batch de
baixa prioridade. ResourceQuota limita consumo por namespace; LimitRange define
padrões. Isso impede um experimento de consumir silenciosamente todo o cluster.

## 9. Dados e workloads

### 9.1 PostgreSQL

O namespace `databases` contém um StatefulSet, Service, PVC, PDB, Secret selado
e NetworkPolicies. Ele atende `postgres-api` e banking. Por usar storage local,
backup lógico externo continua obrigatório.

### 9.2 RabbitMQ

O namespace `messaging` contém StatefulSet, PVCs, PDB, Service, métricas e
credenciais seladas. Filas independentes evitam interferência entre o pipeline
de CPU e o laboratório de memória.

### 9.3 Aplicação bancária

`account-service-java` gerencia contas/saldos e
`transaction-service-dotnet` registra transferências. `Idempotency-Key` impede
duplicação em retries. Ambos expõem métricas de runtime e têm HPA de 1 a 3
réplicas, alvo de CPU de 70%.

### 9.4 postgres-api e nginx-lab

`postgres-api` demonstra Go, banco, mesh, políticas e VPA consultivo.
`nginx-lab` demonstra entrada HTTPS pelo Istio e HPA de 1 a 5, alvo de CPU de
60%.

### 9.5 Pipeline de CPU

O `cpu-producer` publica jobs no RabbitMQ. `cpu-worker` consome a fila e gera
carga controlada. O ScaledObject usa escala de 0 a 8 réplicas, permitindo
paralelismo sem manter consumidores ociosos. Requests baixos facilitam o
agendamento; limits protegem os nós.

```bash
cd /workspace
bash scripts/validation/validate-cpu-pipeline-e2e.sh
```

O teste publica carga, observa scale-up, processamento e retorno a zero.

## 10. Laboratório de memória

O namespace isolado `memory-lab` existe para reproduzir OOM sem pressionar a
memória dos nós nem misturar a fila de CPU.

### 10.1 Configuração e motivo

| Parâmetro | Valor | Motivo |
|---|---:|---|
| réplicas iniciais | 0 | nenhum consumo ocioso |
| KEDA mínimo/máximo | 0/2 | demonstra scale-to-zero e paralelismo seguro |
| polling/cooldown | 5s/90s | reação rápida e scale-down estável |
| gatilho | 2 mensagens por réplica | converte backlog em consumidores |
| request por pod | 20m CPU / 32 MiB | reserva pequena e explícita |
| limit por pod | 200m CPU / 128 MiB | OOM previsível dentro do container |
| quota do namespace | 100m/128Mi requests; 500m/256Mi limits | limita impacto total |
| prioridade | `batch-low` | workloads essenciais vencem sob pressão |
| topology spread | um pod por worker, `DoNotSchedule` | torna a falha de nó observável |

O worker lê mensagens na fila `memory-jobs`. O payload é interpretado como MiB,
alocado temporariamente e mantido por cinco segundos. Uma mensagem acima do
limit provoca `OOMKilled` pelo cgroup do container, não esgotamento global do
nó.

NetworkPolicy começa com deny total e libera apenas DNS e RabbitMQ na porta de
gerenciamento 15672. As credenciais vêm de `SealedSecret`.

### 10.2 Teste KEDA/OOM

```bash
cd /workspace
bash scripts/validation/validate-memory-keda-e2e.sh
```

O teste:

1. confirma estado inicial em zero;
2. cria/reinicia a fila exclusiva;
3. publica carga normal e espera KEDA escalar para dois;
4. publica uma alocação maior que 128 MiB;
5. confirma `OOMKilled` e recuperação pelo Deployment;
6. aguarda drenagem da fila;
7. confirma scale-down para zero;
8. falha se algum nó entrar em `MemoryPressure`.

Também pode ser executado pelo workflow manual `Validate Memory Lab`, que usa o
runner do master e guarda evidência por 14 dias.

### 10.3 Teste de resiliência dos workers

O workflow `Validate Memory Node Resilience` exige
`FAIL-BOTH-WORKERS`. A matriz tem `max-parallel: 1`, portanto nunca interrompe
os dois workers simultaneamente.

Para cada worker, o script:

1. escala dois pods, um em cada nó;
2. cordona o alvo;
3. para kubelet e containerd por SSH;
4. espera o nó deixar de estar `Ready`;
5. remove o pod antigo e verifica substituição no outro worker;
6. restaura os serviços, espera `Ready` e executa `uncordon`;
7. drena a fila e retorna a zero;
8. confirma ausência de `MemoryPressure`.

Um `trap` tenta restaurar serviços e schedulability mesmo quando o teste falha.
O nome do alvo aceita somente `k8s-worker-01` ou `k8s-worker-02`, evitando que
uma variável incorreta direcione SSH para outra máquina. Cada cenário publica
seu próprio artefato.

### 10.4 Memória insuficiente para o scheduler

No workflow `Validate Memory Lab`, selecione o cenário `insufficient-memory` e
informe `TEST-INSUFFICIENT-MEMORY`. O teste cria um pod efêmero que solicita 8 GiB e aceita
somente workers. Como o maior worker possui 6 GiB brutos e menos memória
alocável, o scheduler mantém o pod `Pending` e registra `FailedScheduling` com
`Insufficient memory`.

Esse teste é seguro porque **request não é alocação**: o container não inicia e
nenhuma memória é consumida. O pod permanece por 105 segundos para que Prometheus
colete as métricas e avalie `MemoryLabPodPendingTooLong` e
`MemoryLabPodUnschedulable`. Um `trap` remove o namespace mesmo em falha.

Validações demonstradas:

1. o scheduler usa requests, não o uso instantâneo;
2. KEDA/HPA podem pedir pods, mas não criam capacidade de nó;
3. sem AWS não há Karpenter para criar uma máquina maior;
4. falta de capacidade gera Pending, não OOM;
5. nenhum nó deve entrar em `MemoryPressure`.
## 11. Observabilidade

Prometheus coleta métricas do Kubernetes, nós, Calico, RabbitMQ e aplicações.
Grafana é a camada visual. Loki recebe logs enviados pelo Promtail.

Dashboards versionados relevantes:

- `Kubernetes Complete Overview`: cluster, workloads, Calico e incidentes;
- `Kubernetes / Memory Health`: capacidade, requests, limits, OOM e pressão;
- `Memory Lab / KEDA and OOM`: fila, escala, OOM, memória e reagendamento;
- dashboard do CPU worker/KEDA;
- `Banking / JVM Runtime`;
- `Banking / .NET Runtime`;
- dashboard da `postgres-api`.

O dashboard de memória do lab tem 19 painéis: mensagens, consumidores, réplicas
desejadas/prontas, OOM, atividade KEDA, fila, scale KEDA, working set, uso do
limit, reinícios, memória dos nós, pods por nó, nós Ready, pods Pending, tempo
pendente, estado Pending do experimento, duração do Pending e memória solicitada.

Alertas específicos do memory lab:

- `MemoryWorkerOOMKilled`;
- `MemoryWorkerNearLimit` acima de 80% por um minuto;
- `MemoryJobsBacklogWithoutConsumers`;
- `MemoryWorkerKedaNotScaling`;
- `MemoryJobsQueueNotDrained` por cinco minutos;
- `MemoryLabPodPendingTooLong`;
- `MemoryLabPodUnschedulable`.

Há ainda alertas globais para NodeNotReady, CrashLoopBackOff, throttling de CPU,
container próximo do limit, filesystem, Calico/Typha e MemoryPressure.

Para abrir Grafana, Argo CD e RabbitMQ, siga [access.md](access.md). ClusterIP é
interno; o browser do Windows precisa de túnel SSH.

## 12. Segurança implementada

Camadas complementares:

1. **Sealed Secrets**: somente ciphertext fica no Git;
2. **NetworkPolicy**: começa negando e libera fluxos necessários;
3. **Istio mTLS**: identidade e criptografia entre workloads do mesh;
4. **RBAC/service accounts**: limita chamadas à API;
5. **securityContext**: non-root, seccomp e capabilities reduzidas;
6. **Kyverno Audit**: revela manifests fora do padrão;
7. **imagens imutáveis**: tags baseadas em SHA evitam mudança silenciosa;
8. **quotas/limits**: limitam blast radius de aplicações e testes.

SealedSecret depende da chave privada do controller. Faça backup seguro dela ou
resele todas as credenciais depois de reconstruir o cluster. Nunca copie
credenciais, kubeconfig, chaves SSH ou dumps para o Git.

## 13. CI/CD e runner self-hosted

`Validate IaC` roda em PR e push. Ele verifica Vagrant/Ruby, ShellCheck, YAML,
JSON de dashboards, links Markdown, Kustomize, referências imutáveis, Go, Java
25 e .NET 10.

Pipelines de imagem testam, publicam no GHCR com tag de SHA e abrem outro PR que
atualiza o manifest. O merge desse PR promove a imagem; Argo CD faz o deploy.

Testes que precisam do cluster usam o runner `k8s-master`, com labels
`self-hosted`, `Linux`, `X64` e `master`, protegido pelo environment `lab`.
O binário 2.334.0 e seu SHA-256 estão fixados no Vagrantfile.

Para registrar após reconstrução:

```powershell
$env:ACTIONS_RUNNER_TOKEN = '<token temporário do repositório>'
Set-Location C:\Labs\k8s-vmware\iac\vagrant
vagrant provision k8s-master --provision-with actions-runner
Remove-Item Env:ACTIONS_RUNNER_TOKEN
```

O token é temporário e nunca deve ser salvo. Sem ele, o provisioner instala os
binários, mas não registra um runner novo. Uma instalação existente é apenas
validada e iniciada.

## 14. Ordem recomendada de validação

Execute após provisionamento ou restauração:

```bash
kubectl get nodes
kubectl get tigerastatus
kubectl -n argocd get applications
kubectl get pods -A
kubectl top nodes
cd /workspace
bash scripts/validation/validate-platform-smoke.sh
bash scripts/validation/report-memory-capacity.sh
```

Depois, pelo GitHub Actions:

1. `Validate Cluster`, inicialmente sem CPU E2E;
2. `Validate Cluster` com `run_cpu_e2e=true`;
3. `Validate Memory Lab`;
4. `Validate Memory Node Resilience`, somente numa janela de teste.

Critério final:

- três nós `Ready`, sem pressão;
- aplicações Argo CD `Synced/Healthy`;
- nenhum pod preso em erro ou Pending;
- KEDA retorna consumidores a zero após a fila drenar;
- dashboards recebem séries;
- workflows publicam evidências.

## 15. Operação diária e mudanças

Antes de alterar algo:

```bash
kubectl get nodes
kubectl -n argocd get applications
kubectl get events -A --sort-by=.lastTimestamp | tail -n 40
```

Fluxo de mudança:

1. atualize `main` local;
2. crie uma branch curta;
3. altere manifests e documentação;
4. renderize Kustomize e execute validações;
5. abra PR;
6. aguarde todos os checks;
7. revise o diff;
8. faça squash merge;
9. aguarde Argo CD `Synced/Healthy`;
10. execute o teste funcional proporcional ao risco.

Para manutenção de worker, use `cordon/drain/uncordon`. Não drene o master sem
aceitar indisponibilidade, pois o control plane não é redundante.

## 16. Backup, destruição e reconstrução

Git recupera manifests, mas não recupera automaticamente:

- dados de PVC;
- dump PostgreSQL;
- definições/mensagens RabbitMQ;
- chave privada Sealed Secrets;
- Secret da deploy key Argo CD;
- registro do runner.

Siga [disaster-recovery.md](disaster-recovery.md) antes de executar
`vagrant destroy`. A reconstrução termina somente depois de restaurar dados e
segredos e repetir a ordem de validação da seção 14.

## 17. Mapa de diagnóstico

| Sintoma | Primeiro comando | Causa comum no lab |
|---|---|---|
| VM não encontrada por nome | `vagrant status` em `iac/vagrant` | comando no diretório errado |
| nó NotReady | `kubectl describe node <nó>` | kubelet/containerd ou rede |
| pod Pending | `kubectl describe pod <pod>` | request de memória, quota, PVC ou topology spread |
| ImagePullBackOff | `kubectl describe pod <pod>` | tag ou credencial GHCR |
| app OutOfSync | `kubectl -n argocd get application <app>` | drift, webhook ou mudança manual |
| KEDA não escala | `kubectl get scaledobject,hpa -A` | fila, credencial ou NetworkPolicy |
| dashboard vazio | confira targets no Prometheus | ServiceMonitor, labels ou mTLS |
| browser não abre ClusterIP | teste dentro do master | túnel ausente ou porta incorreta |

O runbook detalhado está em [troubleshooting.md](troubleshooting.md).

## 18. Limites e evolução para produção

O que precisaria mudar:

- três ou mais control planes e etcd redundante;
- storage CSI replicado, snapshots e backups externos;
- load balancer e DNS/PKI confiáveis;
- firewall e SELinux gerenciados;
- Kyverno em Enforce após corrigir violações;
- identidade centralizada e RBAC mínimo;
- gestão externa e rotação automática de segredos;
- Alertmanager com rotas reais e SLOs;
- capacidade baseada em carga e anti-affinity por zona;
- nós dinâmicos via cloud autoscaler/Karpenter, indisponível neste lab local.

KEDA escala pods; ele não cria VMs. Karpenter exigiria uma nuvem como AWS e por
isso não faz parte desta implementação VMware. Quando não há memória em nenhum
worker, o pod permanece Pending mesmo que o KEDA peça mais réplicas — esse é um
dos aprendizados centrais do laboratório.
