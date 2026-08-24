package com.banklab.accounts;

import jakarta.persistence.*;
import java.math.BigDecimal;
import java.util.UUID;

@Entity
@Table(name = "card_credit_lines", schema = "account_service")
public class CardCreditLine {
    @Id
    private UUID accountId;
    @Column(nullable = false, precision = 19, scale = 2)
    private BigDecimal creditLimit;
    @Column(nullable = false, precision = 19, scale = 2)
    private BigDecimal usedAmount;
    @Version
    private long version;

    protected CardCreditLine() {}

    CardCreditLine(UUID accountId, BigDecimal creditLimit) {
        this.accountId = accountId;
        this.creditLimit = creditLimit;
        this.usedAmount = BigDecimal.ZERO.setScale(2);
    }

    boolean canUse(BigDecimal amount) {
        return availableAmount().compareTo(amount) >= 0;
    }

    void use(BigDecimal amount) {
        if (!canUse(amount)) throw new CreditLimitExceededException();
        usedAmount = usedAmount.add(amount);
    }

    public UUID getAccountId() { return accountId; }
    public BigDecimal getCreditLimit() { return creditLimit; }
    public BigDecimal getUsedAmount() { return usedAmount; }
    public BigDecimal availableAmount() { return creditLimit.subtract(usedAmount); }
}
