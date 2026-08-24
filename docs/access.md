# Acessos

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

## Padrão de túnel

Serviços `ClusterIP` não são acessíveis diretamente pelo browser do Windows. O
túnel deve ficar aberto em um terminal e o browser usa `localhost`. Descubra o
IP atual do Service antes de criar o túnel:

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

Descubra o Service:

```bash
kubectl -n argocd get svc argocd-server
```

Crie um túnel da porta local `8085` para a porta HTTPS `443` do ClusterIP e
acesse `https://localhost:8085`. O certificado é autoassinado.

```powershell
ssh.exe -N -i .\iac\vagrant\.vagrant\machines\k8s-master\vmware_desktop\private_key `
  -o IdentitiesOnly=yes -o ExitOnForwardFailure=yes `
  -L 127.0.0.1:8085:<CLUSTER_IP_ARGOCD>:443 `
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

Descubra Service, IP e credencial:

```bash
kubectl -n monitoring get svc -l app.kubernetes.io/name=grafana
kubectl -n monitoring get secret -l app.kubernetes.io/name=grafana
kubectl -n monitoring get secret <SECRET_GRAFANA> \
  -o jsonpath='{.data.admin-user}' | base64 -d; echo
kubectl -n monitoring get secret <SECRET_GRAFANA> \
  -o jsonpath='{.data.admin-password}' | base64 -d; echo
```

Abra um túnel da porta local `3000` para a porta HTTP `80` do ClusterIP e acesse
`http://localhost:3000`. O dashboard `Kubernetes Complete Overview` reúne capacidade, workloads, Calico,
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

## RabbitMQ

O Service é `rabbitmq` no namespace `messaging`. Descubra os NodePorts:

```bash
kubectl -n messaging get svc rabbitmq
```

A interface de gerenciamento usa a porta de serviço `15672`; AMQP usa `5672`.
Prefira um túnel para a interface administrativa e acesse
`http://localhost:15672`.

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

Adicione ao arquivo `hosts` do Windows, se desejar resolução normal:

```text
192.168.109.151 nginx.lab.local
```

Depois acesse `https://nginx.lab.local:31882`. Para testes CLI sem alterar DNS:

```bash
curl -sk --resolve nginx.lab.local:31882:192.168.109.151 \
  https://nginx.lab.local:31882/
```
