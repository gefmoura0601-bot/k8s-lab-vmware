package com.banklab.accounts;

import jakarta.persistence.*;
import java.time.Instant;
import java.util.UUID;

@Entity
@Table(name = "pix_keys", schema = "account_service")
public class PixKey {
    @Id private UUID pixKey;
    @Column(nullable = false, unique = true) private UUID accountId;
    @Column(nullable = false, updatable = false) private Instant createdAt;
    protected PixKey() {}
    public PixKey(UUID pixKey, UUID accountId) {
        this.pixKey = pixKey; this.accountId = accountId; this.createdAt = Instant.now();
    }
    public UUID getPixKey() { return pixKey; }
    public UUID getAccountId() { return accountId; }
    public Instant getCreatedAt() { return createdAt; }
}
