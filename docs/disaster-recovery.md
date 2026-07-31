# Backup e recuperação

## Objetivos

O Git reconstrói o estado declarativo, mas não os dados dos volumes nem as
chaves geradas dentro do cluster. O plano deve separar:

| Categoria | Fonte/backup |
|---|---|
| VMs e manifests | Git + Vagrant |
| Imagens | GHCR |
| PostgreSQL | dump externo |
| RabbitMQ | definitions e, se necessário, backup consistente do volume |
| Chave Sealed Secrets | backup criptografado externo |
| Argo CD repository Secret | cópia segura fora do Git |
| Dashboards | manifests no Git |
| Loki | descartável no lab ou backup do volume |

## Backup de PostgreSQL

Liste banco e pod, depois grave o dump fora do cluster:

```bash
kubectl -n databases get pods
kubectl -n databases exec <postgres-pod> -- \
  pg_dump -U <usuario> -Fc <banco> > postgres.dump
```

Valide o arquivo com `pg_restore --list postgres.dump`. Não mantenha o dump no
repositório.

## Sealed Secrets

Sem a chave privada do controller, os `SealedSecret` existentes não podem ser
descriptografados após uma reconstrução. Faça backup dos Secrets de chave em
cofre externo:

```bash
kubectl -n kube-system get secret \
  -l sealedsecrets.bitnami.com/sealed-secrets-key -o yaml
```

O arquivo contém chave privada: criptografe-o, restrinja acesso e nunca faça
commit. Alternativamente, gere novas credenciais e resele todos os segredos no
cluster reconstruído.

## Reconstrução total

1. restaure/clone a branch `main`;
2. valide rede VMware e recursos;
3. execute `vagrant up`;
4. confirme nós, CNI e storage;
5. restaure a chave Sealed Secrets ou resele credenciais;
6. cadastre a deploy key do repositório no Argo CD;
7. aplique/confirme `platform-root`;
8. aguarde todas as aplicações ficarem saudáveis;
9. restaure PostgreSQL e dados necessários;
10. execute smoke tests e valide dashboards.

## Critérios de aceite

```bash
kubectl get nodes
kubectl get applications -n argocd
kubectl get pods -A
make validate-platform-smoke
make validate-cpu-pipeline-e2e
```

Uma reconstrução só está concluída após validar fluxos de negócio, coleta de
métricas, logs e acesso administrativo.

