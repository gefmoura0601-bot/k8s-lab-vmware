# Kubernetes VMware Lab

Laboratório Kubernetes reproduzível com Vagrant, VMware, kubeadm e Argo CD.

## Arquitetura

- 1 control plane: `192.168.109.151`
- 2 workers: `192.168.109.153` e `192.168.109.155`
- Kubernetes 1.35 com containerd
- Flannel como CNI
- local-path-provisioner para volumes do laboratório
- Argo CD com padrão App of Apps
- Workloads e componentes de plataforma reconciliados a partir da branch `main`

## Pré-requisitos

- VMware Workstation
- Vagrant 2.4+ com provider VMware
- Rede `vmnet8` em `192.168.109.0/24`
- 12 vCPUs e 12 GiB de RAM disponíveis
- Deploy key de leitura cadastrada no repositório privado

## Criação

```powershell
Set-Location iac\vagrant
vagrant validate
vagrant up
```

O provisionamento cria o control plane, adiciona os workers e instala Flannel,
storage local e Argo CD. O kubeconfig fica em
`/home/vagrant/.kube/config` no `k8s-master`.

```powershell
vagrant ssh k8s-master -c "kubectl get nodes"
vagrant ssh k8s-master -c "kubectl get applications -n argocd"
```

## Repositório privado no Argo CD

O root app usa SSH. Cadastre uma deploy key somente leitura no GitHub e crie um
Secret de repositório no namespace `argocd`:

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
    <CHAVE_PRIVADA_DA_DEPLOY_KEY>
```

Nunca versione o Secret preenchido ou a chave privada.

## Fluxo GitHub

Mudanças devem entrar por Pull Request. O workflow `Validate IaC` verifica
Vagrant, shell scripts, referências mutáveis, sintaxe YAML e árvores Kustomize.
O workflow `Reconcile Argo CD` é manual e usa o Environment protegido `lab`.

## Limitações

Este é um laboratório single-control-plane. O storage `local-path` não oferece
alta disponibilidade. Para produção, use control plane HA, storage CSI
distribuído, firewall e SELinux gerenciados, secrets criptografados e backup
externo de etcd.
