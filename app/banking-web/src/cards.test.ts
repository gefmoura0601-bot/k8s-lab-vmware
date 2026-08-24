// @ts-expect-error Vitest runs on Node; production types intentionally exclude Node.
import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";
import {
  CARD_CREDENTIAL_TTL_MS,
  formatCardNumber,
  maskedCardNumber,
} from "./CardsView";

const cardDetails = {
  id: "69bbb2f7-9e75-4dcf-8ac6-e0be74331000",
  type: "CREDIT",
  formFactor: "VIRTUAL",
  status: "ACTIVE",
  holderName: "Cliente Virtual",
  number: "4111111111111111",
  last4: "1111",
  expiryMonth: 12,
  expiryYear: 2029,
  cvv: "123",
  creditLimit: 1000,
  usedAmount: 0,
  availableAmount: 1000,
  createdAt: "2026-08-15T10:00:00Z",
};

afterEach(() => vi.unstubAllGlobals());

function mockJSON(body: unknown, status = 200) {
  const serialized = JSON.stringify(body);
  const fetchMock = vi.fn().mockImplementation(() =>
    Promise.resolve(
      new Response(serialized, {
        status,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("virtual card API", () => {
  it("uses the public same-origin routes and exact issue contract", async () => {
    const fetchMock = mockJSON(cardDetails, 201);
    await api.issueCard("CREDIT", "SenhaSegura2026!");

    const [path, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(path).toBe("/bank/accounts/me/cards");
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("same-origin");
    expect(JSON.parse(String(init.body))).toEqual({
      type: "CREDIT",
      password: "SenhaSegura2026!",
    });
  });

  it("sends only the password when revealing a card", async () => {
    const fetchMock = mockJSON(cardDetails);
    await api.revealCard(cardDetails.id, "SenhaSegura2026!");

    const [path, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(path).toBe(`/bank/accounts/me/cards/${cardDetails.id}/reveal`);
    expect(JSON.parse(String(init.body))).toEqual({ password: "SenhaSegura2026!" });
  });

  it("lists cards and purchases without sending sensitive data", async () => {
    const fetchMock = mockJSON([]);
    await api.cards();
    await api.cardPurchases();
    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      "/bank/accounts/me/cards",
      "/bank/accounts/me/card-purchases",
    ]);
  });

  it("exposes response status for friendly duplicate-CPF handling", async () => {
    mockJSON({ code: "registration_conflict" }, 409);
    await expect(
      api.register("Cliente", "52998224725", "SenhaSegura2026!"),
    ).rejects.toMatchObject({ status: 409, code: "registration_conflict" });
  });

  it("sends CPF only as digits in the registration contract", async () => {
    const fetchMock = mockJSON({
      id: "account-1",
      accountNumber: "12345678",
      ownerName: "Cliente",
      balance: 0,
      cpfMasked: "***.982.247-**",
    }, 201);
    await api.register("Cliente", "52998224725", "SenhaSegura2026!");

    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(JSON.parse(String(init.body))).toEqual({
      ownerName: "Cliente",
      cpf: "52998224725",
      password: "SenhaSegura2026!",
    });
  });
});

describe("credential privacy", () => {
  it("formats only transient values and masks summaries", () => {
    expect(formatCardNumber("4111111111111111")).toBe("4111 1111 1111 1111");
    expect(maskedCardNumber("1111")).toBe("•••• •••• •••• 1111");
    expect(CARD_CREDENTIAL_TTL_MS).toBe(30_000);
  });

  it("does not persist or log credentials in the card component", () => {
    const source = readFileSync(new URL("./CardsView.tsx", import.meta.url), "utf8");
    expect(source).not.toMatch(/localStorage|sessionStorage|console\./);
    expect(source).toContain("visibilitychange");
    expect(source).toContain("pagehide");
  });

  it("does not reveal whether a CPF is already registered", () => {
    const source = readFileSync(new URL("./App.tsx", import.meta.url), "utf8");
    expect(source).toContain(
      "Não foi possível concluir o cadastro; revise os dados ou entre em contato com o suporte.",
    );
    expect(source).not.toContain("CPF já está cadastrado");
  });
});
