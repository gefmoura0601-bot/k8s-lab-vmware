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

## Backup automatizado e validação isolada

O workflow manual `Validate Disaster Recovery` exige a confirmação
`CREATE-AND-VALIDATE-DR-BACKUP` e o secret `DR_BACKUP_PASSPHRASE` no environment
GitHub `lab`. A passphrase deve ter pelo menos 20 caracteres e possuir uma cópia
em cofre externo ao GitHub e ao cluster.

O workflow coleta dump PostgreSQL, representação lógica dos dados, definitions
do RabbitMQ, chave privada do Sealed Secrets, Secrets de repositório do Argo CD,
inventário sanitizado e checksums SHA-256.

O pacote usa AES-256-CBC, PBKDF2 e 200.000 iterações. O `.enc` fica em
`/workspace/.dr-backups`, no disco compartilhado do Windows, e o diretório está
no `.gitignore`. Apenas evidência sanitizada vai para o Actions; dumps, chaves e
Secrets nunca são publicados como artefato.

Depois, o workflow descriptografa em diretório temporário, valida checksums,
cria `dr-restore-validation`, restaura o PostgreSQL com `emptyDir` e compara o
SHA-256 lógico dos dados. Um `trap` remove namespace e temporários mesmo em
falha. A passphrase deve ser guardada também em cofre externo; sem ela, o pacote
é irrecuperável.

Execução manual:

```bash
cd /workspace
export DR_BACKUP_PASSPHRASE='<obtida do cofre>'
bash scripts/validation/create-dr-backup.sh
bash scripts/validation/validate-postgres-dr-restore.sh \
  /workspace/.dr-backups/<bundle>.tar.gz.enc
unset DR_BACKUP_PASSPHRASE
```

Não informe a passphrase como argumento de linha de comando.

Neste host, a cópia de custódia fica em
`.dr-backups/dr-backup-passphrase.dpapi.xml`, protegida pelo DPAPI. Ela só pode
ser aberta pelo mesmo usuário Windows no mesmo perfil. Para recuperar e
reconfigurar o secret sem imprimir o valor:

```powershell
$secure = Import-Clixml .\.dr-backups\dr-backup-passphrase.dpapi.xml
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
    [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) |
        gh.exe secret set DR_BACKUP_PASSPHRASE --env lab
}
finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
}
```

Copie o arquivo DPAPI e os bundles `.enc` para mídia externa protegida. DPAPI
não substitui um cofre corporativo e não sobrevive à perda do perfil Windows.
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
10. execute o workflow `Validate Cluster` e valide os dashboards.

## Critérios de aceite

No GitHub Actions, execute manualmente `Validate Cluster` com `run_cpu_e2e`
habilitado. O workflow valida nodes, aplicações Argo CD, rollouts, mesh/mTLS,
PostgreSQL, RabbitMQ, KEDA, Kyverno e o pipeline de CPU.

Uma reconstrução só está concluída após validar fluxos de negócio, coleta de
métricas, logs e acesso administrativo.
