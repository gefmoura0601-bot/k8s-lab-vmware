package com.banklab.accounts;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import org.junit.jupiter.api.Test;

class CpfServiceTest {
    private final CpfService cpfs = new CpfService(
        "unit-test-banking-identity-secret-with-32-characters");

    @Test
    void requiresDedicatedIdentitySecretWithAtLeast32Characters() {
        assertThatThrownBy(() -> new CpfService("short-identity-secret"))
            .isInstanceOf(IllegalStateException.class)
            .hasMessageContaining("BANKING_IDENTITY_SECRET");
    }

    @Test
    void validatesAndNormalizesFormattedBrazilianCpf() {
        var formatted = cpfs.identity("529.982.247-25");
        var digits = cpfs.identity("52998224725");

        assertThat(formatted.fingerprint()).isEqualTo(digits.fingerprint()).hasSize(64);
        assertThat(formatted.last4()).isEqualTo("4725");
        assertThat(CpfService.masked(formatted.last4())).isEqualTo("***.***.*47-25");
        assertThat(formatted.fingerprint()).doesNotContain("52998224725");
    }

    @Test
    void rejectsRepeatedDigitsWrongCheckDigitsAndUnexpectedCharacters() {
        assertThatThrownBy(() -> cpfs.identity("111.111.111-11"))
            .isInstanceOf(InvalidCpfException.class);
        assertThatThrownBy(() -> cpfs.identity("529.982.247-24"))
            .isInstanceOf(InvalidCpfException.class);
        assertThatThrownBy(() -> cpfs.identity("CPF 529.982.247-25"))
            .isInstanceOf(InvalidCpfException.class);
    }

    @Test
    void producesDifferentFingerprintsForDifferentValidCpfs() {
        assertThat(cpfs.identity("529.982.247-25").fingerprint())
            .isNotEqualTo(cpfs.identity("111.444.777-35").fingerprint());
    }
}
