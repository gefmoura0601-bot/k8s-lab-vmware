package com.banklab.accounts;

import jakarta.persistence.*;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.UUID;

@Entity
@Table(name = "ledger_entries", schema = "account_service")
public class LedgerEntry {
    @Id @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    @Column(nullable = false) private UUID journalId;
    private UUID accountId;
    @Column(nullable = false, precision = 19, scale = 2) private BigDecimal signedAmount;
    @Column(nullable = false, length = 32) private String entryType;
    private UUID reversalOf;
    @Column(nullable = false, updatable = false) private Instant createdAt;
    protected LedgerEntry() {}
    LedgerEntry(UUID journalId, UUID accountId, BigDecimal amount, String type) {
        this(journalId, accountId, amount, type, null);
    }
    LedgerEntry(UUID journalId, UUID accountId, BigDecimal amount, String type, UUID reversalOf) {
        this.journalId=journalId; this.accountId=accountId; this.signedAmount=amount;
        this.entryType=type; this.reversalOf=reversalOf; this.createdAt=Instant.now();
    }
}
