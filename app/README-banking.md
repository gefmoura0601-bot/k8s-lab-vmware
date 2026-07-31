# Banking simulation

The banking sample is split into two independently deployable services:

- `account-service-java`: Java 25 LTS and Spring Boot 4.1; owns accounts and balances.
- `transaction-service-dotnet`: .NET 10 LTS and ASP.NET Core; owns transaction history and idempotent orchestration.

The transaction service calls the account service with the transaction UUID. The account service records that UUID in the same database transaction that locks and updates both accounts. Retrying a request therefore returns the original result without moving money twice.

## Public API

The Istio gateway publishes the services at `https://nginx.lab.local`:

- `POST /bank/accounts`
- `GET /bank/accounts`
- `GET /bank/accounts/{id}`
- `POST /bank/transactions` with an `Idempotency-Key` UUID header
- `GET /bank/transactions/{id}`

Create two accounts:

```bash
curl -sk https://nginx.lab.local:31882/bank/accounts \
  --resolve nginx.lab.local:31882:192.168.109.151 \
  -H 'Content-Type: application/json' \
  -d '{"ownerName":"Alice","initialBalance":1000.00}'
```

Create a transfer:

```bash
curl -sk https://nginx.lab.local:31882/bank/transactions \
  --resolve nginx.lab.local:31882:192.168.109.151 \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: 9ee8d751-4ad0-4d7c-b753-bfbc0a601c27' \
  -d '{
    "sourceAccountId":"SOURCE_UUID",
    "destinationAccountId":"DESTINATION_UUID",
    "amount":125.50,
    "description":"Transfer test"
  }'
```

Repeating the same request with the same idempotency key is safe. Reusing the key with another payload returns HTTP 409.
