# Segurança

## Controles implementados

- repositório privado e imagens no GHCR;
- deploy key somente leitura para o Argo CD;
- Sealed Secrets para dados sensíveis versionados;
- Istio com mTLS estrito nos namespaces de workload;
- exceção `PERMISSIVE` restrita às portas de scrape quando necessária;
- NetworkPolicies com negação padrão e liberações explícitas;
- Kyverno em modo Audit;
- security contexts non-root, seccomp RuntimeDefault e remoção de capabilities;
- RBAC por service account;
- referências de imagens imutáveis nos manifests.

## Segredos

Nunca versione `Secret` em texto claro, kubeconfig, chave SSH, token do GitHub,
senha ou arquivo de diagnóstico contendo dados do usuário.

Fluxo de criação:

```bash
kubectl create secret generic <nome> -n <namespace> \
  --from-literal=<chave>=<valor> --dry-run=client -o yaml |
kubeseal --controller-name sealed-secrets-controller \
  --controller-namespace kube-system --format yaml
```

Salve apenas o `SealedSecret`, valide o namespace/nome e abra PR. Um sealed
secret é ligado à chave do controller e, no modo padrão, também ao nome e
namespace.

## Rotação

Rotacione imediatamente quando houver exposição ou mudança de operador:

- deploy key do Argo CD;
- credencial GHCR;
- credenciais PostgreSQL e RabbitMQ;
- senha administrativa de Grafana e Argo CD;
- certificado TLS.

Faça a rotação por novo `SealedSecret`, aguarde reconciliação, reinicie somente
os consumidores necessários e confirme autenticação antes de revogar a
credencial anterior.

## Diagnósticos

JFR, heap dumps, EventPipe, GC dumps e logs podem conter informações de
aplicação. Mantenha coleta curta, copie apenas para máquina confiável e elimine
o artefato após análise. Os endpoints do `dotnet-monitor` não possuem Service
ou Ingress.

## Limites conhecidos

Este é um laboratório, não uma plataforma de produção:

- control plane e storage sem HA;
- certificado público autoassinado;
- segredos descriptografados existem na API do cluster;
- políticas Kyverno auditam, mas não bloqueiam;
- acesso administrativo por chave local do Vagrant.

Para produção, adicione identidade centralizada, PKI confiável, firewall,
políticas enforce, CSI criptografado, backups externos, auditoria e rotação
automatizada.

