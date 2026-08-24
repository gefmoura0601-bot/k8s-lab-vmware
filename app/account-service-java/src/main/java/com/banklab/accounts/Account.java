package com.banklab.accounts;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import jakarta.persistence.Version;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.UUID;

@Entity
@Table(name = "accounts", schema = "account_service")
public class Account {
    @Id
    private UUID id;
    @Column(nullable = false, length = 120)
    private String ownerName;
    @Column(nullable = false, precision = 19, scale = 2)
    private BigDecimal balance;
    @Column(length = 10, unique = true)
    private String accountNumber;
    @Column(length = 100)
    private String passwordHash;
    @Column(length = 64)
    private String cpfFingerprint;
    @Column(length = 4)
    private String cpfLast4;
    @Column(nullable = false)
    private int failedLoginAttempts;
    private Instant lockedUntil;
    @Column(nullable = false, updatable = false)
    private Instant createdAt;
    @Version
    private long version;

    protected Account() {}

    public Account(UUID id, String ownerName, BigDecimal balance) {
        this.id = id;
        this.ownerName = ownerName;
        this.balance = balance;
        this.createdAt = Instant.now();
    }

    public Account(UUID id, String accountNumber, String ownerName, String passwordHash, BigDecimal balance) {
        this(id, accountNumber, ownerName, passwordHash, balance, null, null);
    }

    public Account(UUID id, String accountNumber, String ownerName, String passwordHash,
                   BigDecimal balance, String cpfFingerprint, String cpfLast4) {
        this(id, ownerName, balance);
        this.accountNumber = accountNumber;
        this.passwordHash = passwordHash;
        this.cpfFingerprint = cpfFingerprint;
        this.cpfLast4 = cpfLast4;
    }

    public boolean hasCredentials() { return accountNumber != null && passwordHash != null; }
    public boolean isLocked(Instant now) { return lockedUntil != null && lockedUntil.isAfter(now); }
    public void loginSucceeded() { failedLoginAttempts = 0; lockedUntil = null; }
    public void loginFailed(Instant now) {
        failedLoginAttempts++;
        if (failedLoginAttempts >= 5) {
            lockedUntil = now.plusSeconds(15 * 60);
            failedLoginAttempts = 0;
        }
    }

    public void debit(BigDecimal amount) {
        if (balance.compareTo(amount) < 0) {
            throw new InsufficientFundsException();
        }
        balance = balance.subtract(amount);
    }

    public void credit(BigDecimal amount) {
        balance = balance.add(amount);
    }

    public UUID getId() { return id; }
    public String getOwnerName() { return ownerName; }
    public BigDecimal getBalance() { return balance; }
    public String getAccountNumber() { return accountNumber; }
    public String getPasswordHash() { return passwordHash; }
    public String getCpfLast4() { return cpfLast4; }
    public Instant getCreatedAt() { return createdAt; }
}
