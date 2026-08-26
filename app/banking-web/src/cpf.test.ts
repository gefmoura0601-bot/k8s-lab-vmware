import { describe, expect, it } from "vitest";
import { cpfDigits, formatCpf, isValidCpf } from "./cpf";

describe("CPF brasileiro", () => {
  it("applies the familiar Brazilian mask without retaining non-digits", () => {
    expect(formatCpf("52998224725")).toBe("529.982.247-25");
    expect(cpfDigits("529.982.247-25")).toBe("52998224725");
    expect(formatCpf("529a982b247c25extra")).toBe("529.982.247-25");
  });

  it("validates both check digits and rejects repeated numbers", () => {
    expect(isValidCpf("529.982.247-25")).toBe(true);
    expect(isValidCpf("111.444.777-35")).toBe(true);
    expect(isValidCpf("529.982.247-24")).toBe(false);
    expect(isValidCpf("111.111.111-11")).toBe(false);
    expect(isValidCpf("123")).toBe(false);
  });
});
