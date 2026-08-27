# Acessos

## URLs permanentes do laboratório

O Istio publica as interfaces na rede privada VMware pelo IP
`192.168.109.151` e pela porta HTTPS `31882`. Não é necessário manter túnel SSH
aberto.

| Componente | URL |
|---|---|
| Grafana | `https://grafana.lab.local:31882` |
| Prometheus | `https://prometheus.lab.local:31882` |
| Argo CD | `https://argocd.lab.local:31882` |
| RabbitMQ Management | `https://rabbitmq.lab.local:31882` |
| Bank Moura | `https://bank-moura.lab.local:31882` |
| APIs legadas do lab | `https://nginx.lab.local:31882` |

Abra um PowerShell como Administrador, na raiz do repositório, e execute uma
vez:

```powershell
.\scripts\configure-lab-urls.ps1
```

O script mantém um bloco próprio no arquivo `hosts`, cria um backup antes de
alterá-lo e limpa o cache DNS. O certificado é emitido pela CA local versionada
em `kubernetes/platform/istio/tls/lab-local-ca.crt`. Para também confiar nessa
CA no Windows e remover o aviso do navegador, use explicitamente:

```powershell
.\scripts\configure-lab-urls.ps1 -TrustCertificate
```

Essa confiança é adequada somente para este laboratório privado. Não publique
esses endpoints na Internet: em particular, o Prometheus não possui uma camada
própria de autenticação.

Valide os cinco endpoints no master com:

```bash
bash /workspace/scripts/validation/validate-permanent-urls.sh
```

## SSH e kubectl

No PowerShell, a partir da raiz do repositório:

```powershell
ssh.exe -i .\iac\vagrant\.vagrant\machines\k8s-master\vmware_desktop\private_key `
  -o IdentitiesOnly=yes vagrant@192.168.109.151
```

Dentro do master:

```bash
kubectl get nodes
kubectl get applications -n argocd
```

O provisionamento também instala o kubeconfig administrativo em `/root/.kube/config`.
Assim, depois de executar `sudo -i`, use normalmente:

```bash
kubectl get nodes
```

## Túnel de contingência

Se o ingress não estiver disponível, ainda é possível acessar um Service
`ClusterIP` por túnel. O túnel deve ficar aberto em um terminal e o browser usa
`localhost`. Descubra o IP atual do Service antes de criá-lo:

```bash
kubectl -n <namespace> get svc <service> -o wide
```

No PowerShell:

```powershell
ssh.exe -N -i .\iac\vagrant\.vagrant\machines\k8s-master\vmware_desktop\private_key `
  -o IdentitiesOnly=yes -o ExitOnForwardFailure=yes `
  -L 127.0.0.1:<PORTA_LOCAL>:<CLUSTER_IP>:<PORTA_SERVICE> `
  vagrant@192.168.109.151
```

Use o IP `192.168.109.151`, pois o nome `k8s-master` só funciona se estiver no
DNS ou em `C:\Windows\System32\drivers\etc\hosts`.

## Argo CD

Acesso principal: `https://argocd.lab.local:31882`.

Descubra o Service:

```bash
kubectl -n argocd get svc argocd-server
```

Como o TLS principal termina no Istio, o Argo CD opera em HTTP dentro do
cluster. Para contingência, crie um túnel da porta local `8085` para a porta
HTTP `80` do ClusterIP e acesse `http://localhost:8085`.

```powershell
ssh.exe -N -i .\iac\vagrant\.vagrant\machines\k8s-master\vmware_desktop\private_key `
  -o IdentitiesOnly=yes -o ExitOnForwardFailure=yes `
  -L 127.0.0.1:8085:<CLUSTER_IP_ARGOCD>:80 `
  vagrant@192.168.109.151
```

Usuário inicial: `admin`. Consulte a senha sem gravá-la em arquivo:

```bash
kubectl -n argocd get secret argocd-initial-admin-secret \
  -o jsonpath='{.data.password}' | base64 -d
echo
```

Troque a senha após o primeiro acesso e remova o Secret inicial quando a
recuperação não for mais necessária.

## Grafana

Acesso principal: `https://grafana.lab.local:31882`.

Descubra Service, IP e credencial:

```bash
kubectl -n monitoring get svc -l app.kubernetes.io/name=grafana
kubectl -n monitoring get secret -l app.kubernetes.io/name=grafana
kubectl -n monitoring get secret <SECRET_GRAFANA> \
  -o jsonpath='{.data.admin-user}' | base64 -d; echo
kubectl -n monitoring get secret <SECRET_GRAFANA> \
  -o jsonpath='{.data.admin-password}' | base64 -d; echo
```

Como contingência, abra um túnel da porta local `3000` para a porta HTTP `80`
do ClusterIP e acesse `http://localhost:3000`. O dashboard
`Kubernetes Complete Overview` reúne capacidade, workloads, Calico,
Typha e a seção `Alertas e Incidentes`, alimentada pelas regras do Prometheus.
Os demais dashboards principais são:

- `Kubernetes / Memory Health`;
- `Memory Lab / KEDA and OOM`;
- dashboard de escala do CPU worker/KEDA;
- dashboard da `postgres-api`;
- `JVM Runtime`;
- `.NET Runtime`.

No dashboard do memory lab, use a janela `Last 15 minutes` durante os workflows
de validação. Os painéis `Pods por node`, `Nodes Ready`, `Pods pendentes` e
`Tempo pendente para reagendamento` mostram o teste de falha dos workers.

## Prometheus

Acesso principal para consulta: `https://prometheus.lab.local:31882`. Não há
autenticação nativa nesse endpoint; ele deve permanecer restrito à rede privada
do laboratório.

Para o assessment executado no master, prefira o endereço interno do Service.
Isso evita depender do DNS do Windows e da confiança na CA local:

```bash
export PROMETHEUS_URL="http://$(kubectl -n monitoring get svc \
  kube-prometheus-stack-prometheus \
  -o jsonpath='{.spec.clusterIP}'):9090"
```

## RabbitMQ

Acesso principal: `https://rabbitmq.lab.local:31882`.

O Service é `rabbitmq` no namespace `messaging`. Descubra suas portas:

```bash
kubectl -n messaging get svc rabbitmq
```

A interface de gerenciamento usa a porta de serviço `15672`; AMQP usa `5672`.
O endpoint permanente publica somente a interface web. O protocolo AMQP não é
exposto por esse Gateway.

As chaves do Secret devem ser inspecionadas antes da leitura:

```bash
kubectl -n messaging get secret rabbitmq-secret \
  -o go-template='{{range $k,$v := .data}}{{$k}}{{"\n"}}{{end}}'
kubectl -n messaging get secret rabbitmq-secret \
  -o jsonpath='{.data.RABBITMQ_DEFAULT_USER}' | base64 -d; echo
kubectl -n messaging get secret rabbitmq-secret \
  -o jsonpath='{.data.RABBITMQ_DEFAULT_PASS}' | base64 -d; echo
```

## APIs pelo Istio

O script de configuração mantém este bloco no arquivo `hosts` do Windows:

```text
192.168.109.151 nginx.lab.local grafana.lab.local prometheus.lab.local argocd.lab.local rabbitmq.lab.local bank-moura.lab.local
```

Use `https://bank-moura.lab.local:31882` para a aplicação web e
`https://nginx.lab.local:31882` para os caminhos históricos do laboratório.
Para testes CLI sem alterar DNS:

```bash
curl -sk --resolve nginx.lab.local:31882:192.168.109.151 \
  https://nginx.lab.local:31882/
```
