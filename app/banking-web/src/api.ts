import type { Account, ApiError, Transaction } from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {"Content-Type": "application/json", ...init?.headers}
  });
  if (!response.ok) {
    const error = (await response.json().catch(() => ({}))) as ApiError;
    throw new Error(error.message ?? `Falha HTTP ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export const api = {
  listAccounts: () => request<Account[]>("/bank/accounts"),
  createAccount: (ownerName: string, initialBalance: number) =>
    request<Account>("/bank/accounts", {method: "POST", body: JSON.stringify({ownerName, initialBalance})}),
  createTransaction: (sourceAccountId: string, destinationAccountId: string, amount: number, description: string) => {
    const idempotencyKey = crypto.randomUUID();
    return request<Transaction>("/bank/transactions", {
      method: "POST",
      headers: {"Idempotency-Key": idempotencyKey},
      body: JSON.stringify({sourceAccountId, destinationAccountId, amount, description})
    });
  },
  getTransaction: (id: string) => request<Transaction>(`/bank/transactions/${id}`)
};

export function money(value: number) {
  return new Intl.NumberFormat("pt-BR", {style: "currency", currency: "BRL"}).format(value);
}
