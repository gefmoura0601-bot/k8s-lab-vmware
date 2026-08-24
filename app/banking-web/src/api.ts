import type {
  Account,
  ApiError,
  CardDetails,
  CardPurchase,
  CardSummary,
  CardType,
  DirectoryEntry,
  PixKey,
  Transaction,
} from "./types";

export class ApiRequestError extends Error {
  constructor(
    readonly status: number,
    readonly code: string | undefined,
    message: string,
  ) {
    super(message);
    this.name = "ApiRequestError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, {
    ...init,
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!r.ok) {
    const e = (await r.json().catch(() => ({}))) as ApiError;
    throw new ApiRequestError(r.status, e.code, e.message ?? `Falha HTTP ${r.status}`);
  }
  if (r.status === 204) return undefined as T;
  return r.json() as Promise<T>;
}
export const api = {
  me: () => request<Account>("/bank/accounts/me"),
  directory: () => request<DirectoryEntry[]>("/bank/accounts/directory"),
  pixKey: () => request<PixKey>("/bank/accounts/me/pix-key"),
  createPixKey: () =>
    request<PixKey>("/bank/accounts/me/pix-key", { method: "PUT" }),
  login: (accountNumber: string, password: string) =>
    request<Account>("/bank/auth/login", {
      method: "POST",
      body: JSON.stringify({ accountNumber, password }),
    }),
  register: (ownerName: string, cpf: string, password: string) =>
    request<Account>("/bank/auth/register", {
      method: "POST",
      body: JSON.stringify({ ownerName, cpf, password }),
    }),
  logout: () => request<void>("/bank/auth/logout", { method: "POST" }),
  statement: (sourceAccountId: string) =>
    request<Transaction[]>(
      `/bank/transactions?sourceAccountId=${sourceAccountId}`,
    ),
  transfer: (
    sourceAccountId: string,
    destination: { destinationAccountId?: string; pixKey?: string },
    amount: number,
    description: string,
    password: string,
  ) =>
    request<Transaction>("/bank/transactions", {
      method: "POST",
      headers: { "Idempotency-Key": crypto.randomUUID() },
      body: JSON.stringify({
        sourceAccountId,
        ...destination,
        amount,
        description,
        password,
      }),
    }),
  transaction: (id: string) => request<Transaction>(`/bank/transactions/${id}`),
  cards: () => request<CardSummary[]>("/bank/accounts/me/cards"),
  issueCard: (type: CardType, password: string) =>
    request<CardDetails>("/bank/accounts/me/cards", {
      method: "POST",
      body: JSON.stringify({ type, password }),
    }),
  revealCard: (cardId: string, password: string) =>
    request<CardDetails>(`/bank/accounts/me/cards/${encodeURIComponent(cardId)}/reveal`, {
      method: "POST",
      body: JSON.stringify({ password }),
    }),
  cardPurchases: () => request<CardPurchase[]>("/bank/accounts/me/card-purchases"),
};
export const money = (v: number) =>
  new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" }).format(
    v,
  );
