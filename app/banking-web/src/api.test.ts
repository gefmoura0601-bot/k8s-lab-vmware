// @ts-expect-error Vitest runs on Node; production types intentionally exclude Node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { money } from "./api";

describe("money", () => {
  it("formats Brazilian currency", () => {
    expect(money(125.5)).toContain("125,50");
  });
});

describe("interface encoding", () => {
  it("keeps Portuguese text as valid UTF-8", () => {
    const source = readFileSync(new URL("./App.tsx", import.meta.url), "utf8");
    expect(source).not.toContain(String.fromCharCode(0xfffd));
    expect(source).toMatch(/Transfer\u00eancia/);
    expect(source).toMatch(/N\u00famero da conta/);
    expect(source).toMatch(/Sess\u00e3o protegida/);
  });
});

describe("PIX API", () => {
  it("supports key management and PIX destinations", () => {
    const source = readFileSync(new URL("./api.ts", import.meta.url), "utf8");
    expect(source).toContain("/bank/accounts/me/pix-key");
    expect(source).toContain("pixKey?: string");
  });
});
