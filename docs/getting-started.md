# Preparação e provisionamento

## Pré-requisitos no Windows

- VMware Workstation;
- Vagrant 2.4+ e Vagrant VMware Utility/provider;
- Git, OpenSSH e, opcionalmente, GitHub CLI;
- rede `vmnet8` em `192.168.109.0/24`;
- pelo menos 12 vCPU, 16 GiB de RAM e espaço para três VMs;
- acesso de leitura ao repositório privado e ao GHCR.

No Windows são necessárias apenas as ferramentas nativas listadas acima. Não é
preciso instalar um subsistema Linux, Kubernetes, Go, ShellCheck ou Make.
Builds e testes automatizados executam no GitHub Actions; comandos de cluster
executam por SSH no `k8s-master`.

## Provisionar

No PowerShell:

```powershell
Set-Location C:\Labs\k8s-vmware\iac\vagrant
vagrant validate
vagrant up
vagrant status
```

O provisionamento configura IPs, DNS, containerd, kubeadm, Calico,
local-path-provisioner e Argo CD. O kubeconfig administrativo fica em
`/home/vagrant/.kube/config` no master.

Valide:

```powershell
vagrant ssh k8s-master -c "kubectl get nodes -o wide"
vagrant ssh k8s-master -c "kubectl get applications -n argocd"
```

## Repositório privado

O Argo CD acessa o repositório por SSH. Cadastre uma deploy key somente leitura
no GitHub e registre a chave privada como Secret no namespace `argocd`. Não
versione a chave:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: repo-k8s-lab-vmware
  namespace: argocd
  labels:
    argocd.argoproj.io/secret-type: repository
stringData:
  type: git
  url: git@github.com:gefmoura0601-bot/k8s-lab-vmware.git
  sshPrivateKey: |
    <CHAVE_PRIVADA>
```

Para imagens privadas, `ghcr-secret` é armazenado como `SealedSecret`. Ele deve
ser selado com a chave do controller do cluster atual; após reconstruir o
cluster, consulte [disaster-recovery.md](disaster-recovery.md).

## Operação das VMs

```powershell
Set-Location C:\Labs\k8s-vmware\iac\vagrant
vagrant status
vagrant halt
vagrant up
vagrant reload k8s-worker-02 --provision-with network
```

O provisioner `network` sempre reaplica o IP estático e DNS. Evite alterar as
interfaces manualmente dentro da VM sem refletir a mudança no Vagrantfile.

## Troca do CNI

O estado desejado usa Calico com VXLAN e o pool de pods `10.244.0.0/16`. Não
tente substituir o CNI em um cluster ativo: rotas, interfaces e pods existentes
continuam vinculados ao provedor anterior. Faça os backups descritos em
[disaster-recovery.md](disaster-recovery.md) e reconstrua as VMs:

```powershell
Set-Location C:\Labs\k8s-vmware\iac\vagrant
vagrant destroy
vagrant up
```

Depois da reconstrução, confirme `TigerStatus/calico`, nodes e aplicações:

```powershell
vagrant ssh k8s-master -c "kubectl get tigerastatus; kubectl get nodes"
```

Por fim, execute o workflow manual `Validate Cluster` no GitHub Actions. O
workflow deve permanecer falhando em clusters antigos que ainda usem outro CNI.

## Destruição

`vagrant destroy` elimina as VMs e os volumes locais. Faça backup primeiro e
confirme explicitamente os alvos. A operação é apropriada somente quando a
reconstrução completa estiver planejada.
