# Migrations da postgres-api

## Objetivo

O trace distribuído revelou que `GET /users` chegava ao PostgreSQL, mas falhava
porque a tabela `users` não existia. A migration V1 transforma o esquema em
código versionado e garante que o banco esteja pronto antes de uma nova revisão
da API avançar.

## Ordem GitOps

O Argo CD usa ondas de sincronização:

1. onda `-1`: ConfigMap da aplicação, SealedSecret e SQL da migration;
2. onda `0`: Job `postgres-api-migration-v1`;
3. onda `1`: Rollout da `postgres-api`.

Foi usado um Job normal, e não `PreSync`, porque no primeiro bootstrap um hook
PreSync executaria antes do Secret que contém a credencial existir. O Argo CD
aguarda o Job terminar antes de avançar para a onda seguinte, preservando a
garantia desejada sem depender de estado anterior do cluster.

## Migration V1

`migration-v1-users.yaml` contém o SQL e o Job executor. A transação cria:

- `schema_migrations`, que registra versão, descrição e data de aplicação;
- `users`, com identidade, nome, e-mail único e data de criação;
- índice por `created_at` para consultas cronológicas.

`CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS` e
`ON CONFLICT DO NOTHING` tornam a execução repetível. `psql` usa
`ON_ERROR_STOP=1`, portanto qualquer comando inválido faz o Job falhar e impede
o Rollout de começar. A imagem `postgres:17-alpine` traz somente o cliente
necessário, e o pod recebe limites baixos por ser temporário.

Para futuras alterações, crie outro arquivo e outro Job imutável, por exemplo
`migration-v2-add-user-status.yaml`. Nunca altere uma migration já aplicada;
adicione uma versão nova.

## Validação

```bash
kubectl -n apps logs job/postgres-api-migration-v1
kubectl -n apps get job postgres-api-migration-v1
kubectl -n databases exec postgres-0 -- sh -ec \
  'PGPASSWORD="$POSTGRES_PASSWORD" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -c "TABLE schema_migrations"'
bash scripts/validation/validate-tracing-e2e.sh
```

O E2E agora exige dez respostas HTTP 200, encontra um trace que contenha
`postgresql.users.select` e reprova se o span estiver com
`STATUS_CODE_ERROR`. Assim, não basta produzir telemetria: a operação precisa
funcionar corretamente de ponta a ponta.
