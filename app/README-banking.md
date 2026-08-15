# Simulação bancária

A aplicação demonstra uma conta corrente com criação de contas, consulta de
saldo e transferências idempotentes entre dois serviços:

| Serviço | Tecnologia | Responsabilidade |
|---|---|---|
| `account-service` | Java 25 / Spring Boot 4.1 | Contas, saldos e aplicação atômica da transferência |
| `transaction-service` | .NET 10 / ASP.NET Core | Histórico e orquestração idempotente |
| `banking-web` | React 19 / TypeScript / Nginx | Interface para contas e transferências |

Os serviços executam no namespace `banking`, usam PostgreSQL no namespace
`databases` e são publicados pelo Istio em `https://nginx.lab.local:31882`.

A interface está disponível em `https://nginx.lab.local:31882/banking/`. O
histórico mostrado nela contém as transações criadas no navegador atual, pois a
API expõe consulta por ID, mas não uma listagem global.

## Consistência

O cliente envia uma UUID no header `Idempotency-Key`. O serviço .NET chama o
serviço Java com essa UUID; o Java registra a chave na mesma transação de banco
que bloqueia e atualiza as duas contas. Repetir o mesmo request retorna o
resultado original sem mover dinheiro novamente. Reutilizar a chave com outro
payload retorna HTTP 409.

Este desenho protege retries, mas não substitui um ledger contábil completo,
conciliação ou trilha regulatória.

## API

| Método | Endpoint | Descrição |
|---|---|---|
| `POST` | `/bank/auth/register` | Cria conta e sessão segura |
| `POST` | `/bank/auth/login` | Autentica por conta e senha |
| `GET` | `/bank/accounts/me` | Consulta somente a conta autenticada |
| `GET` | `/bank/accounts/me/pix-key` | Consulta a chave PIX da conta autenticada |
| `PUT` | `/bank/accounts/me/pix-key` | Cria ou retorna idempotentemente a chave PIX da conta |
| `POST` | `/bank/transactions` | Executa transferência após confirmar a senha |
| `GET` | `/bank/transactions?sourceAccountId={id}` | Extrato persistente da conta autenticada |
| `GET` | `/bank/transactions/{id}` | Consulta transferência da conta autenticada |

Ao criar uma transferência, informe exatamente um destino: `destinationAccountId`
para uma conta conhecida ou `pixKey` para resolução pelo diretório PIX interno.
Nos dois casos a senha da conta de origem é confirmada antes da resolução e da
movimentação financeira.

Crie duas contas:

```bash
curl -sk https://nginx.lab.local:31882/bank/accounts \
  --resolve nginx.lab.local:31882:192.168.109.151 \
  -H 'Content-Type: application/json' \
  -d '{"ownerName":"Alice","initialBalance":1000.00}'

curl -sk https://nginx.lab.local:31882/bank/accounts \
  --resolve nginx.lab.local:31882:192.168.109.151 \
  -H 'Content-Type: application/json' \
  -d '{"ownerName":"Bob","initialBalance":500.00}'
```

Transfira valores usando os UUIDs retornados:

```bash
curl -sk https://nginx.lab.local:31882/bank/transactions \
  --resolve nginx.lab.local:31882:192.168.109.151 \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: 9ee8d751-4ad0-4d7c-b753-bfbc0a601c27' \
  -d '{
    "sourceAccountId":"SOURCE_UUID",
    "destinationAccountId":"DESTINATION_UUID",
    "amount":125.50,
    "description":"Transferência de teste"
  }'
```

## Operação

```bash
kubectl -n banking get deploy,pod,svc,hpa
kubectl -n banking logs deploy/account-service -c account-service --tail=100
kubectl -n banking logs deploy/transaction-service \
  -c transaction-service --tail=100
```

Os manifests ficam em `kubernetes/apps/banking`. Alterações no código disparam
o workflow `Banking Images CI`, que publica imagens imutáveis no GHCR e abre um
PR atualizando as referências GitOps.

Para métricas, profiling e coleta de JFR/EventPipe, consulte
[`docs/runtime-observability.md`](../docs/runtime-observability.md).

## Ledger contábil

Novas contas começam com saldo zero. Cada transferência grava, na mesma transação do saldo, um débito e um crédito com o mesmo `journal_id`; um constraint trigger rejeita journals cuja soma não seja zero. Saldos legados são migrados como crédito de abertura e contrapartida sistêmica. A métrica `banking_ledger_divergent_accounts` e o alerta `BankingLedgerDivergence` detectam divergência entre o saldo materializado e os lançamentos. O campo `reversal_of` reserva estornos compensatórios sem apagar o histórico; a operação administrativa de estorno não é exposta ao cliente.
