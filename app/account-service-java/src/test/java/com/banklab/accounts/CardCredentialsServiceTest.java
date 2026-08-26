package com.banklab.accounts;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.math.BigDecimal;
import java.util.UUID;
import org.junit.jupiter.api.Test;

class CardCredentialsServiceTest {
    private final CardCredentialsService credentials = new CardCredentialsService(
        "unit-test-banking-card-secret-with-32-characters");

    @Test
    void derivesDeterministicLabPanWithValidLuhnCheckDigit() {
        var cardId = UUID.fromString("69bbb2f7-9e75-4dcf-8ac6-e0be74331000");

        var first = credentials.pan(cardId);
        var second = credentials.pan(cardId);

        assertThat(first).isEqualTo(second).startsWith("999999").hasSize(16);
        assertThat(credentials.validPan(first)).isTrue();
        assertThat(credentials.validPan(first.substring(0, 15) + (first.endsWith("0") ? "1" : "0")))
            .isFalse();
    }

    @Test
    void requiresDedicatedCardSecretWithAtLeast32Characters() {
        assertThatThrownBy(() -> new CardCredentialsService("short-card-secret"))
            .isInstanceOf(IllegalStateException.class)
            .hasMessageContaining("BANKING_CARD_SECRET");
    }

    @Test
    void derivesCvvAndLookupFingerprintWithoutPersistableCredentials() {
        var cardId = UUID.randomUUID();
        var pan = credentials.pan(cardId);
        var cvv = credentials.cvv(cardId);

        assertThat(cvv).matches("\\d{3}");
        assertThat(credentials.validCvv(cardId, cvv)).isTrue();
        assertThat(credentials.validCvv(cardId, "999".equals(cvv) ? "998" : "999")).isFalse();
        assertThat(credentials.panFingerprint(pan))
            .hasSize(64)
            .doesNotContain(pan)
            .doesNotContain(cvv);
    }

    @Test
    void paymentFingerprintChangesWhenPayloadChanges() {
        var paymentId = UUID.randomUUID();
        var card = new CardService.PaymentCard("9999990000000005", "Alice", 12, 2029, "123");
        var original = new CardService.CardPaymentRequest(paymentId, "merchant", "Store", "order-1",
            new BigDecimal("10.00"), "BRL", card, CardType.DEBIT, 1);
        var changed = new CardService.CardPaymentRequest(paymentId, "merchant", "Store", "order-1",
            new BigDecimal("11.00"), "BRL", card, CardType.DEBIT, 1);

        assertThat(credentials.paymentFingerprint(original))
            .isNotEqualTo(credentials.paymentFingerprint(changed));
    }
}
