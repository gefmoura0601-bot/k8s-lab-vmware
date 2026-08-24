package com.banklab.accounts;

import jakarta.persistence.*;
import java.time.Instant;
import java.util.UUID;

@Entity
@Table(name = "cards", schema = "account_service")
public class Card {
    @Id
    private UUID id;
    @Column(nullable = false)
    private UUID accountId;
    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 10)
    private CardType cardType;
    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 10)
    private CardFormFactor formFactor;
    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 12)
    private CardStatus status;
    @Column(nullable = false, unique = true, length = 64)
    private String panFingerprint;
    @Column(nullable = false)
    private int expiryMonth;
    @Column(nullable = false)
    private int expiryYear;
    @Column(nullable = false, updatable = false)
    private Instant createdAt;
    @Version
    private long version;

    protected Card() {}

    Card(UUID id, UUID accountId, CardType cardType, String panFingerprint,
         int expiryMonth, int expiryYear) {
        this.id = id;
        this.accountId = accountId;
        this.cardType = cardType;
        this.formFactor = CardFormFactor.VIRTUAL;
        this.status = CardStatus.ACTIVE;
        this.panFingerprint = panFingerprint;
        this.expiryMonth = expiryMonth;
        this.expiryYear = expiryYear;
        this.createdAt = Instant.now();
    }

    public UUID getId() { return id; }
    public UUID getAccountId() { return accountId; }
    public CardType getCardType() { return cardType; }
    public CardFormFactor getFormFactor() { return formFactor; }
    public CardStatus getStatus() { return status; }
    public String getPanFingerprint() { return panFingerprint; }
    public int getExpiryMonth() { return expiryMonth; }
    public int getExpiryYear() { return expiryYear; }
    public Instant getCreatedAt() { return createdAt; }
}

enum CardType { DEBIT, CREDIT }
enum CardFormFactor { VIRTUAL, PHYSICAL }
enum CardStatus { ACTIVE, BLOCKED, CANCELLED }
