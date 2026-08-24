package com.banklab.accounts;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

import java.math.BigDecimal;
import java.util.Optional;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

@ExtendWith(MockitoExtension.class)
class CardServiceTest {
    @Mock AccountRepository accounts;
    @Mock CardRepository cards;
    @Mock CardCreditLineRepository creditLines;
    @Mock CardPaymentRepository payments;
    @Mock LedgerEntryRepository ledger;
    @Mock CardMetrics metrics;
    private CardCredentialsService credentials;
    private CardService service;

    @BeforeEach
    void setUp() {
        credentials = new CardCredentialsService(
            "unit-test-banking-card-secret-with-32-characters");
        service = new CardService(accounts, cards, creditLines, payments, ledger, credentials, metrics);
    }

    @Test
    void issuesOneVirtualDebitCardWithoutPersistingCredentials() {
        var account = account("Alice", "100.00");
        when(accounts.findByIdForUpdate(account.getId())).thenReturn(Optional.of(account));
        when(cards.findByAccountIdAndCardTypeAndFormFactor(
            account.getId(), CardType.DEBIT, CardFormFactor.VIRTUAL)).thenReturn(Optional.empty());
        when(cards.save(any(Card.class))).thenAnswer(invocation -> invocation.getArgument(0));

        var result = service.issue(account.getId(), CardType.DEBIT);

        assertThat(result.type()).isEqualTo(CardType.DEBIT);
        assertThat(result.formFactor()).isEqualTo(CardFormFactor.VIRTUAL);
        assertThat(result.number()).matches("999999\\d{10}");
        assertThat(result.cvv()).matches("\\d{3}");
        assertThat(result.availableAmount()).isEqualByComparingTo("100.00");
        verify(cards).save(argThat(card ->
            card.getPanFingerprint().length() == 64
                && !card.getPanFingerprint().contains(result.number())
                && !card.getPanFingerprint().contains(result.cvv())));
        verifyNoInteractions(creditLines);
        verify(metrics).issued(CardType.DEBIT);
    }

    @Test
    void issuesCreditCardWithFixedLabLimit() {
        var account = account("Alice", "0.00");
        when(accounts.findByIdForUpdate(account.getId())).thenReturn(Optional.of(account));
        when(cards.findByAccountIdAndCardTypeAndFormFactor(
            account.getId(), CardType.CREDIT, CardFormFactor.VIRTUAL)).thenReturn(Optional.empty());
        when(cards.save(any(Card.class))).thenAnswer(invocation -> invocation.getArgument(0));
        when(creditLines.findById(account.getId())).thenReturn(Optional.empty());
        when(creditLines.save(any(CardCreditLine.class))).thenAnswer(invocation -> invocation.getArgument(0));

        var result = service.issue(account.getId(), CardType.CREDIT);

        assertThat(result.creditLimit()).isEqualByComparingTo("1000.00");
        assertThat(result.usedAmount()).isEqualByComparingTo("0.00");
        assertThat(result.availableAmount()).isEqualByComparingTo("1000.00");
    }

    @Test
    void capturesDebitCardPaymentWithBalancedLedger() {
        var account = account("Alice", "100.00");
        var card = card(account, CardType.DEBIT);
        var request = request(card, account, CardType.DEBIT, "25.00");
        arrangeNewPayment(request, card, account);

        var result = service.authorizeAndCapture(request);

        assertThat(result.status()).isEqualTo(CardPaymentStatus.CAPTURED);
        assertThat(result.authorizationCode()).matches("\\d{6}");
        assertThat(account.getBalance()).isEqualByComparingTo("75.00");
        verify(ledger, times(2)).save(any(LedgerEntry.class));
        verify(metrics).paymentCaptured(CardType.DEBIT);
    }

    @Test
    void declinesDebitPaymentWithoutFundsAndDoesNotWriteLedger() {
        var account = account("Alice", "10.00");
        var card = card(account, CardType.DEBIT);
        var request = request(card, account, CardType.DEBIT, "25.00");
        arrangeNewPayment(request, card, account);

        var result = service.authorizeAndCapture(request);

        assertThat(result.status()).isEqualTo(CardPaymentStatus.DECLINED);
        assertThat(result.declineCode()).isEqualTo("INSUFFICIENT_FUNDS");
        assertThat(account.getBalance()).isEqualByComparingTo("10.00");
        verifyNoInteractions(ledger);
        verify(metrics).paymentDeclined(CardType.DEBIT);
    }

    @Test
    void capturesCreditPaymentAgainstFixedLimitWithoutDebitingCheckingBalance() {
        var account = account("Alice", "0.00");
        var card = card(account, CardType.CREDIT);
        var line = new CardCreditLine(account.getId(), CardService.CREDIT_LIMIT);
        var request = request(card, account, CardType.CREDIT, "250.00");
        arrangeNewPayment(request, card, account);
        when(creditLines.findByIdForUpdate(account.getId())).thenReturn(Optional.of(line));

        var result = service.authorizeAndCapture(request);

        assertThat(result.status()).isEqualTo(CardPaymentStatus.CAPTURED);
        assertThat(line.getUsedAmount()).isEqualByComparingTo("250.00");
        assertThat(line.availableAmount()).isEqualByComparingTo("750.00");
        assertThat(account.getBalance()).isEqualByComparingTo("0.00");
        verifyNoInteractions(ledger);
    }

    @Test
    void returnsPriorResultForIdenticalIdempotentRetry() {
        var account = account("Alice", "100.00");
        var card = card(account, CardType.DEBIT);
        var request = request(card, account, CardType.DEBIT, "25.00");
        var fingerprint = credentials.paymentFingerprint(request);
        var prior = CardPayment.captured(request, card, "123456", fingerprint);
        when(payments.findById(request.paymentId())).thenReturn(Optional.of(prior));

        var result = service.authorizeAndCapture(request);

        assertThat(result.status()).isEqualTo(CardPaymentStatus.CAPTURED);
        assertThat(result.authorizationCode()).isEqualTo("123456");
        verifyNoInteractions(cards, accounts, ledger, creditLines);
    }

    @Test
    void rejectsIdempotencyKeyReusedWithDifferentPayload() {
        var account = account("Alice", "100.00");
        var card = card(account, CardType.DEBIT);
        var original = request(card, account, CardType.DEBIT, "25.00");
        var changed = new CardService.CardPaymentRequest(original.paymentId(), original.merchantId(),
            original.merchantName(), original.orderReference(), new BigDecimal("26.00"),
            original.currency(), original.card(), original.paymentType(), original.installments());
        var prior = CardPayment.captured(original, card, "123456",
            credentials.paymentFingerprint(original));
        when(payments.findById(changed.paymentId())).thenReturn(Optional.of(prior));

        assertThatThrownBy(() -> service.authorizeAndCapture(changed))
            .isInstanceOf(IdempotencyConflictException.class);
        verifyNoInteractions(cards, accounts, ledger, creditLines);
    }

    private void arrangeNewPayment(CardService.CardPaymentRequest request, Card card, Account account) {
        when(payments.findById(request.paymentId())).thenReturn(Optional.empty());
        when(cards.findByPanFingerprintForUpdate(credentials.panFingerprint(request.card().number())))
            .thenReturn(Optional.of(card));
        when(accounts.findByIdForUpdate(account.getId())).thenReturn(Optional.of(account));
        when(payments.save(any(CardPayment.class))).thenAnswer(invocation -> invocation.getArgument(0));
    }

    private Card card(Account account, CardType type) {
        var id = UUID.randomUUID();
        return new Card(id, account.getId(), type,
            credentials.panFingerprint(credentials.pan(id)), 12, 2029);
    }

    private CardService.CardPaymentRequest request(Card card, Account account, CardType type, String amount) {
        return new CardService.CardPaymentRequest(UUID.randomUUID(), "moura-shop", "Moura Shop",
            "order-1", new BigDecimal(amount), "BRL",
            new CardService.PaymentCard(credentials.pan(card.getId()), account.getOwnerName(),
                card.getExpiryMonth(), card.getExpiryYear(), credentials.cvv(card.getId())),
            type, 1);
    }

    private static Account account(String owner, String balance) {
        return new Account(UUID.randomUUID(), "00000001", owner, "hash",
            new BigDecimal(balance));
    }
}
