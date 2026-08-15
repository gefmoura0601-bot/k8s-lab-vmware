import { describe, expect, it } from "vitest";
import { money } from "./api";

describe("money", () => {
  it("formats Brazilian currency", () => {
    expect(money(125.5)).toContain("125,50");
  });
});
