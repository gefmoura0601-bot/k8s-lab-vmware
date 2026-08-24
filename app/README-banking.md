# Simulação bancária

A aplicação demonstra contas correntes, PIX, cartões virtuais e uma adquirência
de laboratório. Todo o fluxo é publicado pelo Istio em
`https://nginx.lab.local:31882`:

| Serviço | Tecnologia | Responsabilidade |
|---|---|---|
| `account-service` | Java 25 / Spring Boot 4.1 | Contas, identidade por CPF, saldo, ledger, PIX e emissão/autorização de cartões |
| `transaction-service` | .NET 10 / ASP.NET Core | Histórico e orquestração idempotente de transferências |
| `acquirer-service` | .NET 10 / ASP.NET Core | Adquirência simulada, idempotência e encaminhamento ao emissor |
| `store-service` | Go 1.24 / biblioteca padrão | Catálogo, checkout e loja de teste |
| `banking-web` | React 19 / TypeScript / Nginx | Interface para conta, PIX, transferências e cartões |

Os serviços executam no namespace `banking` e usam PostgreSQL no namespace
`databases`. A interface bancária fica em
`https://nginx.lab.local:31882/banking/` e a loja em
`https://nginx.lab.local:31882/store/`.

## Cadastro e identidade por CPF

Novos cadastros exigem `ownerName`, `cpf` e `password`. A interface aplica a
máscara brasileira e envia somente os 11 dígitos; o backend normaliza o valor,
valida os dígitos verificadores e usa uma fingerprint HMAC-SHA-256 com chave
secreta para garantir unicidade. O CPF completo não é persistido: ficam apenas
a fingerprint e os quatro últimos dígitos necessários para retornar
`cpfMasked`.

A migração V7 deixa `cpf_fingerprint` e `cpf_last4` anuláveis para preservar
contas legadas. Essas contas continuam válidas e podem retornar
`cpfMasked: null`; a nulabilidade não torna o CPF opcional para novos
cadastros. Conflitos de cadastro retornam uma mensagem genérica, sem confirmar
se determinado CPF já existe.

O segredo usado na fingerprint precisa permanecer estável. Trocá-lo sem migrar
as fingerprints existentes quebraria a detecção de duplicidade.

## Cartões virtuais e loja

Uma conta pode emitir um cartão virtual `DEBIT` e um `CREDIT`. A emissão é
idempotente por conta, tipo e fator de forma; a senha da sessão é confirmada
antes de emitir ou revelar as credenciais. Listagens retornam apenas dados
mascarados. Emissão e revelação usam `Cache-Control: no-store`, e a interface
mantém PAN/CVV somente em memória, limpando-os ao sair da tela ou após 30
segundos.

Os cartões são exclusivamente sintéticos:

- PAN com exatamente 16 dígitos, BIN `999999` e checksum Luhn válido;
- CVV com exatamente 3 dígitos;
- fator de forma `VIRTUAL`;
- limite de crédito fixo de laboratório de R$ 1.000,00 por conta;
- débito limitado ao saldo disponível da conta.

O BIN `999999` não representa uma bandeira real. Não informe cartões reais na
loja, nos testes ou nas evidências. PAN e CVV são derivados de forma
determinística pelo emissor; o banco persiste somente fingerprints e metadados,
e a adquirência persiste os quatro últimos dígitos e a fingerprint do request.

O fluxo de compra é:

```text
Browser
  -> store-service (catálogo e total recalculado no servidor)
  -> acquirer-service (idempotência e decisão da adquirência)
  -> account-service (emissor: valida cartão, saldo ou limite)
  -> ledger de débito ou linha de crédito
```

A loja mantém um catálogo fixo em BRL e ignora preço, merchant ou total enviados
pelo cliente. Cada checkout exige uma UUID em `Idempotency-Key`. Repetir o
mesmo request retorna a mesma decisão; reutilizar a chave com outro payload
retorna HTTP 409 sem ecoar detalhes internos. As respostas ao browser contêm
somente status, identificadores, código de autorização/recusa, tipo e últimos
quatro dígitos.

## Consistência

Transferências também exigem uma UUID em `Idempotency-Key`. O serviço .NET
confirma a senha, resolve o destino e chama o serviço Java com essa UUID. O Java
registra a chave na mesma transação que bloqueia e atualiza as contas. Repetir o
mesmo request não move dinheiro novamente; reutilizar a chave com outro payload
retorna HTTP 409.

Compras de cartão usam a UUID do checkout como `orderId` na loja, ID de
pagamento na adquirência e `paymentId` no emissor. A adquirência e o emissor
comparam fingerprints do request antes de reutilizar uma decisão.

Esse desenho protege retries, mas não substitui um ledger e uma conciliação
regulatórios completos.

## API pública

Os endpoints abaixo são publicados pelo gateway. Rotas `/bank/*` usam a sessão
segura `moura_session` quando indicado.

| Método | Endpoint | Descrição |
|---|---|---|
| `POST` | `/bank/auth/register` | Cria conta por `ownerName`, `cpf` e `password`, e inicia a sessão |
| `POST` | `/bank/auth/login` | Autentica por número da conta e senha |
| `POST` | `/bank/auth/logout` | Encerra a sessão |
| `GET` | `/bank/accounts/me` | Consulta somente a conta autenticada |
| `GET` | `/bank/accounts/directory` | Lista destinos visíveis para transferência |
| `GET` | `/bank/accounts/me/pix-key` | Consulta a chave PIX da conta |
| `PUT` | `/bank/accounts/me/pix-key` | Cria ou retorna idempotentemente a chave PIX |
| `POST` | `/bank/accounts/me/cards` | Emite cartão virtual `DEBIT` ou `CREDIT` após confirmar a senha |
| `GET` | `/bank/accounts/me/cards` | Lista cartões mascarados e saldo/uso disponível |
| `POST` | `/bank/accounts/me/cards/{cardId}/reveal` | Revela temporariamente PAN/CVV após confirmar a senha |
| `GET` | `/bank/accounts/me/card-purchases` | Lista compras capturadas e recusadas da conta |
| `POST` | `/bank/transactions` | Executa transferência por conta ou PIX após confirmar a senha |
| `GET` | `/bank/transactions?sourceAccountId={id}` | Lista o extrato persistente da conta autenticada |
| `GET` | `/bank/transactions/{id}` | Consulta uma transferência da conta autenticada |
| `GET` | `/store/api/catalog` | Retorna o catálogo fixo e os preços em BRL |
| `POST` | `/store/api/checkout` | Compra com cartão do laboratório; exige `Idempotency-Key` UUID |

Ao criar uma transferência, informe exatamente um destino:
`destinationAccountId` para uma conta conhecida ou `pixKey` para resolução
pelo diretório PIX interno.

## API interna

As rotas internas não são publicadas para clientes. As AuthorizationPolicies e
identidades mTLS restringem cada chamada ao ServiceAccount esperado.

| Origem | Método e endpoint interno | Finalidade |
|---|---|---|
| `transaction-service` -> `account-service` | `POST /internal/v1/auth/authorize` | Autoriza leitura do extrato |
| `transaction-service` -> `account-service` | `POST /internal/v1/auth/confirm` | Confirma senha antes da transferência |
| `transaction-service` -> `account-service` | `GET /internal/v1/pix-keys/{pixKey}` | Resolve uma chave PIX |
| `transaction-service` -> `account-service` | `POST /internal/v1/transfers` | Aplica débito/crédito atômico |
| `transaction-service` -> `account-service` | `POST /internal/v1/transfers/{id}/reversals` | Aplica estorno compensatório |
| `store-service` -> `acquirer-service` | `POST /internal/v1/payments` | Cria ou repete um pagamento idempotente |
| `store-service` -> `acquirer-service` | `GET /internal/v1/payments/{id}` | Consulta pagamento da adquirência |
| `acquirer-service` -> `account-service` | `POST /internal/v1/card-payments` | Autoriza e captura no emissor |

## Segredos estáveis

Os valores pertencem a Secrets/SealedSecrets e nunca devem ser colocados neste
README, em logs ou em artefatos de teste. Todos precisam ser iguais entre
réplicas e estáveis durante rollouts:

| Variável | Uso | Consequência de rotação sem migração |
|---|---|---|
| `BANKING_SESSION_SECRET` | Assina a sessão HTTP | Invalida sessões ativas |
| `BANKING_IDENTITY_SECRET` | Deriva fingerprints HMAC de CPF | Perde a correspondência com fingerprints existentes |
| `BANKING_CARD_SECRET` | Deriva PAN, CVV, fingerprints e autorizações do laboratório | Altera credenciais e impede localizar cartões/pagamentos existentes |
| `ACQUIRER_IDEMPOTENCY_SECRET` | Deriva a fingerprint idempotente da adquirência | Faz retries de pagamentos retidos divergirem |

Rotação exige um plano compatível com os dados persistidos — migração,
re-emissão ou expiração controlada, conforme o segredo — e não apenas reiniciar
os pods.

## Limitações do crédito no MVP

O cartão de crédito serve para validar integração e concorrência, não para
simular um produto financeiro completo:

- o limite é fixo em R$ 1.000,00 por conta, sem análise ou alteração;
- o valor usado apenas acumula; ainda não há fatura, ciclo, vencimento,
  pagamento, liberação de limite, juros ou tarifas;
- parcelas são registradas como metadado, sem agenda de recebíveis ou cobranças;
- autorização e captura acontecem juntas e de forma síncrona;
- não existem pré-autorização, cancelamento, refund, chargeback, clearing ou
  conciliação de cartões;
- somente cartões virtuais são emitidos; cartão físico não faz parte do fluxo;
- o ambiente não é PCI DSS e jamais deve processar PAN/CVV reais.

## Testes locais

Use Java 25/Maven, .NET SDK 10, Go 1.24 ou mais recente e Node.js 24. A partir da raiz do
repositório:

```bash
cd app/account-service-java
mvn --batch-mode verify

cd ../acquirer-service-dotnet
dotnet restore
dotnet build --configuration Release --no-restore
dotnet test tests/AcquirerService.Tests --configuration Release

cd ../store-service-go
gofmt -w main.go main_test.go
go vet ./...
go test ./...

cd ../banking-web
npm ci --ignore-scripts
npm run test
npm run build
```

Esses testes usam somente identidades e cartões sintéticos. Não copie CPF, PAN,
CVV ou senhas reais para fixtures.

## Testes E2E

Com o cluster do laboratório estável e `kubectl`, `curl`, `jq` e `awk`
disponíveis no control plane:

```bash
bash scripts/validation/validate-banking-pix-e2e.sh
bash scripts/validation/validate-banking-cards-e2e.sh
```

O E2E de cartões cria uma conta com CPF sintético, emite cartões de débito e
crédito, compra na loja, valida idempotência e conflito, exercita recusas por
CVV/saldo, confere saldo/limite/histórico e remove os dados temporários em um
`trap`. PAN, CVV e CPF não são gravados nas evidências. O workflow manual
`Validate Banking Cards` exige a confirmação `TEST-BANKING-CARDS`; o PIX usa
o workflow correspondente e a confirmação `TEST-BANKING-PIX`.

## Operação

```bash
kubectl -n banking get deploy,pod,svc,hpa
kubectl -n banking logs deploy/account-service -c account-service --tail=100
kubectl -n banking logs deploy/transaction-service -c transaction-service --tail=100
kubectl -n banking logs deploy/acquirer-service -c acquirer-service --tail=100
kubectl -n banking logs deploy/store-service -c store-service --tail=100
```

Os manifests ficam em `kubernetes/apps/banking`. Alterações no código disparam
os workflows de imagens bancárias, que publicam imagens imutáveis no GHCR e
abrem um PR atualizando as referências GitOps.

Para métricas, profiling e coleta de JFR/EventPipe, consulte
[`docs/runtime-observability.md`](../docs/runtime-observability.md).

## Ledger contábil

Novas contas começam com saldo zero. Cada transferência grava, na mesma
transação do saldo, um débito e um crédito com o mesmo `journal_id`; um
constraint trigger rejeita journals cuja soma não seja zero. Saldos legados são
migrados como crédito de abertura e contrapartida sistêmica. A métrica
`banking_ledger_divergent_accounts` e o alerta `BankingLedgerDivergence`
detectam divergência entre o saldo materializado e os lançamentos.

Compras no débito também geram lançamentos balanceados. Crédito usa a linha de
crédito do MVP e não altera o saldo da conta. O campo `reversal_of` reserva
estornos compensatórios de transferências sem apagar o histórico; a operação
administrativa de estorno não é exposta ao cliente.
