# Troubleshooting

## SSH: `Permission denied (publickey)`

Use o usuário `vagrant`, a chave específica da VM e
`-o IdentitiesOnly=yes`. Na WSL, copie a chave para um arquivo temporário e
aplique `chmod 600`, conforme [access.md](access.md). A senha do usuário não
substitui a chave criada pelo Vagrant.

Para diagnóstico:

```bash
ssh -vvv -i <chave> -o IdentitiesOnly=yes \
  vagrant@192.168.109.151
```

## `Could not resolve hostname k8s-master`

O Windows não conhece esse nome por padrão. Use `192.168.109.151` ou adicione
uma entrada ao arquivo `hosts`.

## kubectl tenta `localhost:8080`

Não há kubeconfig válido no usuário atual:

```bash
export KUBECONFIG=/home/vagrant/.kube/config
kubectl config current-context
kubectl cluster-info
```

## Túnel abre, mas o browser não conecta

1. confirme que o processo SSH continua aberto;
2. use `127.0.0.1`, não o IP ClusterIP, no browser;
3. redescubra o ClusterIP após recriação do Service;
4. confirme porta HTTP/HTTPS;
5. teste o Service dentro do master;
6. use `-o ExitOnForwardFailure=yes` para detectar porta local ocupada.

```powershell
Get-NetTCPConnection -LocalPort 3000 -ErrorAction SilentlyContinue
```

## Nó `NotReady`

```bash
kubectl describe node <node>
kubectl get events -A --sort-by=.lastTimestamp | tail -n 60
sudo systemctl status kubelet containerd --no-pager
sudo journalctl -u kubelet -n 200 --no-pager
df -h
free -h
```

Se um worker receber IP DHCP adicional ou perder DNS, execute no Windows:

```powershell
vagrant reload <worker> --provision-with network
```

## Pod `Pending`

Cheque eventos, requests, taints, PVC e quota:

```bash
kubectl -n <ns> describe pod <pod>
kubectl describe nodes
kubectl -n <ns> get pvc
kubectl -n <ns> get resourcequota,limitrange
```

No worker de 4 GiB, falta de memória alocável é uma causa comum mesmo quando o
uso instantâneo parece baixo: o scheduler considera requests.

## `ImagePullBackOff`

```bash
kubectl -n <ns> describe pod <pod>
kubectl -n <ns> get secret ghcr-secret
kubectl -n <ns> get serviceaccount <sa> -o yaml
```

Confirme tag existente, nome lowercase no GHCR, vínculo do imagePullSecret e
token ainda válido. Depois de reconstruir o controller Sealed Secrets, resele a
credencial para a nova chave.

## Argo CD `OutOfSync`

Use o runbook em [gitops-cicd.md](gitops-cicd.md). Não ignore campos inteiros
para ocultar drift. Verifique primeiro admission webhooks, status de CRDs,
defaulting da API e alterações manuais.

## Dashboard vazio

```bash
kubectl -n monitoring get pods
kubectl -n banking get servicemonitor
kubectl -n banking get pods -o wide
kubectl -n banking logs <pod> -c <container> --tail=100
```

No Prometheus, confirme que os targets Java/.NET estão `UP`. Confira namespace,
labels do ServiceMonitor, porta nomeada, path de métricas e
PeerAuthentication. Se apenas um pod estiver vazio, selecione `All` na variável
do dashboard e valide se ele foi recriado recentemente.

## Certificado autoassinado

O aviso TLS em Argo CD e no endpoint Istio é esperado no lab. Confirme o
fingerprint antes de confiar. Não confunda aviso de certificado com falha de
rede ou autenticação.

