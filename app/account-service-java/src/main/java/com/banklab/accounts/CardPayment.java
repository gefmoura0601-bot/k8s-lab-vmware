package com.banklab.accounts;

import jakarta.persistence.*;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.UUID;

@Entity
@Table(name = "card_payments", schema = "account_service")
public class CardPayment {
    @Id
    private UUID paymentId;
    private UUID cardId;
    private UUID accountId;
    @Column(nullable = false, length = 80)
    private String merchantId;
    @Column(nullable = false, length = 120)
    private String merchantName;
    @Column(nullable = false, length = 120)
    private String orderReference;
    @Column(nullable = false, precision = 19, scale = 2)
    private BigDecimal amount;
    @Column(nullable = false, length = 3)
    private String currency;
    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 10)
    private CardType paymentType;
    @Enumerated(EnumType.STRING)
    @Column(length = 10)
    private CardType cardType;
    @Column(nullable = false)
    private int installments;
    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 12)
    private CardPaymentStatus status;
    @Column(length = 6)
    private String authorizationCode;
    @Column(length = 40)
    private String declineCode;
    @Column(nullable = false, length = 64)
    private String requestFingerprint;
    @Column(nullable = false, updatable = false)
    private Instant createdAt;
    @Column(nullable = false)
    private Instant completedAt;

    protected CardPayment() {}

    private CardPayment(UUID paymentId, UUID cardId, UUID accountId, String merchantId,
                        String merchantName, String orderReference, BigDecimal amount,
                        String currency, CardType paymentType, CardType cardType, int installments,
                        CardPaymentStatus status, String authorizationCode, String declineCode,
                        String requestFingerprint) {
        this.paymentId = paymentId;
        this.cardId = cardId;
        this.accountId = accountId;
        this.merchantId = merchantId;
        this.merchantName = merchantName;
        this.orderReference = orderReference;
        this.amount = amount;
        this.currency = currency;
        this.paymentType = paymentType;
        this.cardType = cardType;
        this.installments = installments;
        this.status = status;
        this.authorizationCode = authorizationCode;
        this.declineCode = declineCode;
        this.requestFingerprint = requestFingerprint;
        this.createdAt = Instant.now();
        this.completedAt = this.createdAt;
    }

    static CardPayment captured(CardService.CardPaymentRequest request, Card card,
                                String authorizationCode, String requestFingerprint) {
        return new CardPayment(request.paymentId(), card.getId(), card.getAccountId(),
            request.merchantId().trim(), request.merchantName().trim(), request.orderReference().trim(),
            request.amount(), request.normalizedCurrency(), request.paymentType(), card.getCardType(),
            request.installments(), CardPaymentStatus.CAPTURED, authorizationCode, null,
            requestFingerprint);
    }

    static CardPayment declined(CardService.CardPaymentRequest request, Card card,
                                String declineCode, String requestFingerprint) {
        return new CardPayment(request.paymentId(), card == null ? null : card.getId(),
            card == null ? null : card.getAccountId(), request.merchantId().trim(),
            request.merchantName().trim(), request.orderReference().trim(), request.amount(),
            request.normalizedCurrency(), request.paymentType(), card == null ? null : card.getCardType(),
            request.installments(), CardPaymentStatus.DECLINED, null, declineCode,
            requestFingerprint);
    }

    public UUID getPaymentId() { return paymentId; }
    public UUID getCardId() { return cardId; }
    public UUID getAccountId() { return accountId; }
    public String getMerchantId() { return merchantId; }
    public String getMerchantName() { return merchantName; }
    public String getOrderReference() { return orderReference; }
    public BigDecimal getAmount() { return amount; }
    public String getCurrency() { return currency; }
    public CardType getPaymentType() { return paymentType; }
    public CardType getCardType() { return cardType; }
    public int getInstallments() { return installments; }
    public CardPaymentStatus getStatus() { return status; }
    public String getAuthorizationCode() { return authorizationCode; }
    public String getDeclineCode() { return declineCode; }
    public String getRequestFingerprint() { return requestFingerprint; }
    public Instant getCreatedAt() { return createdAt; }
    public Instant getCompletedAt() { return completedAt; }
}

enum CardPaymentStatus { CAPTURED, DECLINED }
