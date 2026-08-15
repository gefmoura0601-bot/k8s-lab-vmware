package com.banklab.accounts;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.UUID;

@Entity
@Table(name = "processed_transfers", schema = "account_service")
public class TransferRecord {
    @Id
    private UUID transactionId;
    @Column(nullable = false)
    private UUID sourceAccountId;
    @Column(nullable = false)
    private UUID destinationAccountId;
    @Column(nullable = false, precision = 19, scale = 2)
    private BigDecimal amount;
    private UUID reversalOf;
    @Column(nullable = false, updatable = false)
    private Instant processedAt;

    protected TransferRecord() {}

    public TransferRecord(UUID transactionId, UUID sourceAccountId, UUID destinationAccountId, BigDecimal amount) {
        this(transactionId, sourceAccountId, destinationAccountId, amount, null);
    }

    public TransferRecord(UUID transactionId, UUID sourceAccountId, UUID destinationAccountId,
                          BigDecimal amount, UUID reversalOf) {
        this.transactionId = transactionId;
        this.sourceAccountId = sourceAccountId;
        this.destinationAccountId = destinationAccountId;
        this.amount = amount;
        this.reversalOf = reversalOf;
        this.processedAt = Instant.now();
    }

    public UUID getTransactionId() { return transactionId; }
    public UUID getSourceAccountId() { return sourceAccountId; }
    public UUID getDestinationAccountId() { return destinationAccountId; }
    public BigDecimal getAmount() { return amount; }
    public UUID getReversalOf() { return reversalOf; }
}
