package com.banklab.accounts;

import jakarta.validation.Valid;
import jakarta.validation.constraints.*;
import java.math.BigDecimal;
import java.time.Instant;
import java.time.YearMonth;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Locale;
import java.util.UUID;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class CardService {
    static final BigDecimal CREDIT_LIMIT = new BigDecimal("1000.00");
    private final AccountRepository accounts;
    private final CardRepository cards;
    private final CardCreditLineRepository creditLines;
    private final CardPaymentRepository payments;
    private final LedgerEntryRepository ledger;
    private final CardCredentialsService credentials;
    private final CardMetrics metrics;

    CardService(AccountRepository accounts, CardRepository cards,
                CardCreditLineRepository creditLines, CardPaymentRepository payments,
                LedgerEntryRepository ledger, CardCredentialsService credentials,
                CardMetrics metrics) {
        this.accounts = accounts;
        this.cards = cards;
        this.creditLines = creditLines;
        this.payments = payments;
        this.ledger = ledger;
        this.credentials = credentials;
        this.metrics = metrics;
    }

    @Transactional
    CardDetails issue(UUID accountId, CardType type) {
        var account = accounts.findByIdForUpdate(accountId).orElseThrow(AccountNotFoundException::new);
        var existing = cards.findByAccountIdAndCardTypeAndFormFactor(
            accountId, type, CardFormFactor.VIRTUAL);
        if (existing.isPresent()) {
            var creditLine = type == CardType.CREDIT ? ensureCreditLine(accountId) : null;
            return details(existing.get(), account, creditLine);
        }

        var expiry = YearMonth.now(ZoneOffset.UTC).plusYears(3);
        for (var attempt = 0; attempt < 5; attempt++) {
            var id = UUID.randomUUID();
            var fingerprint = credentials.panFingerprint(credentials.pan(id));
            if (cards.existsByPanFingerprint(fingerprint)) continue;
            var card = cards.save(new Card(id, accountId, type, fingerprint,
                expiry.getMonthValue(), expiry.getYear()));
            var creditLine = type == CardType.CREDIT ? ensureCreditLine(accountId) : null;
            metrics.issued(type);
            return details(card, account, creditLine);
        }
        throw new IllegalStateException("Unable to allocate a unique virtual card");
    }

    @Transactional(readOnly = true)
    List<CardSummary> list(UUID accountId) {
        var account = accounts.findById(accountId).orElseThrow(AccountNotFoundException::new);
        return cards.findAllByAccountIdOrderByCreatedAtDesc(accountId).stream()
            .map(card -> summary(card, account))
            .toList();
    }

    @Transactional(readOnly = true)
    CardDetails reveal(UUID accountId, UUID cardId) {
        var account = accounts.findById(accountId).orElseThrow(AccountNotFoundException::new);
        var card = ownedCard(accountId, cardId);
        metrics.revealed(card.getCardType());
        return details(card, account);
    }

    @Transactional(readOnly = true)
    List<CardPurchaseView> purchases(UUID accountId) {
        accounts.findById(accountId).orElseThrow(AccountNotFoundException::new);
        return payments.findAllByAccountIdOrderByCreatedAtDesc(accountId).stream()
            .map(CardPurchaseView::from)
            .toList();
    }

    @Transactional
    PaymentResult authorizeAndCapture(CardPaymentRequest request) {
        payments.acquirePaymentLock(request.paymentId());
        var requestFingerprint = credentials.paymentFingerprint(request);
        var lastFour = credentials.lastFour(request.card().number());
        var existing = payments.findById(request.paymentId());
        if (existing.isPresent()) {
            if (!credentials.sameFingerprint(existing.get().getRequestFingerprint(), requestFingerprint)) {
                throw new IdempotencyConflictException();
            }
            return result(existing.get(), request.paymentType(), lastFour);
        }

        if (!"BRL".equals(request.normalizedCurrency())) {
            return decline(request, null, "UNSUPPORTED_CURRENCY", requestFingerprint, lastFour);
        }
        if (request.paymentType() == CardType.DEBIT && request.installments() != 1) {
            return decline(request, null, "INVALID_INSTALLMENTS", requestFingerprint, lastFour);
        }
        if (!credentials.validPan(request.card().number())) {
            return decline(request, null, "INVALID_CARD", requestFingerprint, lastFour);
        }

        var card = cards.findByPanFingerprintForUpdate(
            credentials.panFingerprint(request.card().number())).orElse(null);
        if (card == null) {
            return decline(request, null, "INVALID_CARD", requestFingerprint, lastFour);
        }
        if (card.getStatus() != CardStatus.ACTIVE) {
            return decline(request, card, "CARD_INACTIVE", requestFingerprint, lastFour);
        }
        if (card.getCardType() != request.paymentType()) {
            return decline(request, card, "PAYMENT_TYPE_MISMATCH", requestFingerprint, lastFour);
        }
        var account = accounts.findByIdForUpdate(card.getAccountId())
            .orElseThrow(AccountNotFoundException::new);
        if (!sameHolder(account.getOwnerName(), request.card().holderName())) {
            return decline(request, card, "CARDHOLDER_MISMATCH", requestFingerprint, lastFour);
        }
        if (expired(card, request.card())) {
            return decline(request, card, "EXPIRED_CARD", requestFingerprint, lastFour);
        }
        if (!credentials.validCvv(card.getId(), request.card().cvv())) {
            return decline(request, card, "INVALID_SECURITY_CODE", requestFingerprint, lastFour);
        }

        if (card.getCardType() == CardType.DEBIT) {
            if (account.getBalance().compareTo(request.amount()) < 0) {
                return decline(request, card, "INSUFFICIENT_FUNDS", requestFingerprint, lastFour);
            }
            account.debit(request.amount());
            ledger.save(new LedgerEntry(request.paymentId(), account.getId(),
                request.amount().negate(), "CARD_PURCHASE_DEBIT"));
            ledger.save(new LedgerEntry(request.paymentId(), null,
                request.amount(), "CARD_ACQUIRER_CREDIT"));
        } else {
            var creditLine = creditLines.findByIdForUpdate(account.getId())
                .orElseThrow(CreditLineNotFoundException::new);
            if (!creditLine.canUse(request.amount())) {
                return decline(request, card, "CREDIT_LIMIT_EXCEEDED", requestFingerprint, lastFour);
            }
            creditLine.use(request.amount());
        }

        var authorizationCode = credentials.authorizationCode(request.paymentId());
        var payment = payments.save(CardPayment.captured(
            request, card, authorizationCode, requestFingerprint));
        metrics.paymentCaptured(card.getCardType());
        return result(payment, card.getCardType(), lastFour);
    }

    private PaymentResult decline(CardPaymentRequest request, Card card, String declineCode,
                                  String requestFingerprint, String lastFour) {
        var payment = payments.save(CardPayment.declined(
            request, card, declineCode, requestFingerprint));
        metrics.paymentDeclined(card == null ? request.paymentType() : card.getCardType());
        return result(payment, request.paymentType(), lastFour);
    }

    private Card ownedCard(UUID accountId, UUID cardId) {
        var card = cards.findById(cardId).orElseThrow(CardNotFoundException::new);
        if (!accountId.equals(card.getAccountId())) throw new CardNotFoundException();
        return card;
    }

    private CardCreditLine ensureCreditLine(UUID accountId) {
        return creditLines.findById(accountId)
            .orElseGet(() -> creditLines.save(new CardCreditLine(accountId, CREDIT_LIMIT)));
    }

    private CardSummary summary(Card card, Account account) {
        return summary(card, account, null);
    }

    private CardSummary summary(Card card, Account account, CardCreditLine suppliedCreditLine) {
        var number = credentials.pan(card.getId());
        if (card.getCardType() == CardType.DEBIT) {
            return new CardSummary(card.getId(), card.getCardType(), card.getFormFactor(),
                card.getStatus(), credentials.lastFour(number), card.getExpiryMonth(),
                card.getExpiryYear(), null, null, account.getBalance(), card.getCreatedAt());
        }
        var line = suppliedCreditLine == null
            ? creditLines.findById(account.getId()).orElseThrow(CreditLineNotFoundException::new)
            : suppliedCreditLine;
        return new CardSummary(card.getId(), card.getCardType(), card.getFormFactor(),
            card.getStatus(), credentials.lastFour(number), card.getExpiryMonth(),
            card.getExpiryYear(), line.getCreditLimit(), line.getUsedAmount(),
            line.availableAmount(), card.getCreatedAt());
    }

    private CardDetails details(Card card, Account account) {
        return details(card, account, null);
    }

    private CardDetails details(Card card, Account account, CardCreditLine creditLine) {
        var summary = summary(card, account, creditLine);
        return new CardDetails(summary.id(), summary.type(), summary.formFactor(), summary.status(),
            account.getOwnerName(), credentials.pan(card.getId()), summary.last4(),
            summary.expiryMonth(), summary.expiryYear(), credentials.cvv(card.getId()),
            summary.creditLimit(), summary.usedAmount(), summary.availableAmount(),
            summary.createdAt());
    }

    private static boolean sameHolder(String expected, String supplied) {
        return CardCredentialsService.normalizeText(expected)
            .equals(CardCredentialsService.normalizeText(supplied));
    }

    private static boolean expired(Card card, PaymentCard supplied) {
        if (card.getExpiryMonth() != supplied.expiryMonth()
            || card.getExpiryYear() != supplied.expiryYear()) return true;
        return YearMonth.of(card.getExpiryYear(), card.getExpiryMonth())
            .isBefore(YearMonth.now(ZoneOffset.UTC));
    }

    private static PaymentResult result(CardPayment payment, CardType fallbackType, String lastFour) {
        return new PaymentResult(payment.getPaymentId(), payment.getStatus(),
            payment.getAuthorizationCode(), payment.getDeclineCode(),
            payment.getCardType() == null ? fallbackType : payment.getCardType(), lastFour);
    }

    record CardSummary(UUID id, CardType type, CardFormFactor formFactor, CardStatus status,
                       String last4, int expiryMonth, int expiryYear,
                       BigDecimal creditLimit, BigDecimal usedAmount,
                       BigDecimal availableAmount, Instant createdAt) {}

    record CardDetails(UUID id, CardType type, CardFormFactor formFactor, CardStatus status,
                       String holderName, String number, String last4, int expiryMonth,
                       int expiryYear, String cvv, BigDecimal creditLimit,
                       BigDecimal usedAmount, BigDecimal availableAmount, Instant createdAt) {}

    record CardPurchaseView(UUID paymentId, UUID cardId, String merchantId, String merchantName,
                            String orderReference, BigDecimal amount, String currency,
                            CardType paymentType, int installments, CardPaymentStatus status,
                            String authorizationCode, String declineCode, Instant createdAt) {
        static CardPurchaseView from(CardPayment payment) {
            return new CardPurchaseView(payment.getPaymentId(), payment.getCardId(),
                payment.getMerchantId(), payment.getMerchantName(), payment.getOrderReference(),
                payment.getAmount(), payment.getCurrency(), payment.getPaymentType(),
                payment.getInstallments(), payment.getStatus(), payment.getAuthorizationCode(),
                payment.getDeclineCode(), payment.getCreatedAt());
        }
    }

    record CardPaymentRequest(
        @NotNull UUID paymentId,
        @NotBlank @Size(max = 80) String merchantId,
        @NotBlank @Size(max = 120) String merchantName,
        @NotBlank @Size(max = 120) String orderReference,
        @NotNull @DecimalMin("0.01") @Digits(integer = 17, fraction = 2) BigDecimal amount,
        @NotBlank @Size(min = 3, max = 3) String currency,
        @NotNull @Valid PaymentCard card,
        @NotNull CardType paymentType,
        @Min(1) @Max(12) int installments) {
        String normalizedCurrency() {
            return currency == null ? "" : currency.trim().toUpperCase(Locale.ROOT);
        }
    }

    record PaymentCard(
        @NotBlank @Size(max = 32) String number,
        @NotBlank @Size(max = 120) String holderName,
        @Min(1) @Max(12) int expiryMonth,
        @Min(2020) @Max(9999) int expiryYear,
        @NotBlank @Size(max = 12) String cvv) {}

    record PaymentResult(UUID paymentId, CardPaymentStatus status, String authorizationCode,
                         String declineCode, CardType cardType, String last4) {}
}
