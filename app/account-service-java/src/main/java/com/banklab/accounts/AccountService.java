package com.banklab.accounts;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.List;
import java.util.UUID;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class AccountService {
    private final AccountRepository accounts;
    private final TransferRecordRepository transfers;
    private final LedgerEntryRepository ledger;

    AccountService(AccountRepository accounts, TransferRecordRepository transfers, LedgerEntryRepository ledger) {
        this.accounts = accounts;
        this.transfers = transfers;
        this.ledger = ledger;
    }

    @Transactional
    public Account create(String ownerName, BigDecimal initialBalance) {
        var balance = money(initialBalance);
        if (balance.signum() < 0) {
            throw new InvalidTransferException("initialBalance must not be negative");
        }
        return accounts.save(new Account(UUID.randomUUID(), ownerName.trim(), balance));
    }

    @Transactional
    public Account create(String number, String owner, String passwordHash, BigDecimal initialBalance) {
        var balance = money(initialBalance);
        if (balance.signum() < 0) throw new InvalidTransferException("initialBalance must not be negative");
        var account = accounts.save(new Account(UUID.randomUUID(), number, owner.trim(), passwordHash, balance));
        if (balance.signum() != 0) {
            var journalId = UUID.randomUUID();
            ledger.save(new LedgerEntry(journalId, account.getId(), balance, "WELCOME_CREDIT"));
            ledger.save(new LedgerEntry(journalId, null, balance.negate(), "SYSTEM_OFFSET"));
        }
        return account;
    }

    @Transactional(readOnly = true)
    public List<Account> directory() {
        return accounts.findAll().stream().filter(Account::hasCredentials).toList();
    }

    @Transactional(readOnly = true)
    public Account get(UUID id) {
        return accounts.findById(id).orElseThrow(AccountNotFoundException::new);
    }

    @Transactional(readOnly = true)
    public List<Account> list() {
        return accounts.findAll();
    }

    @Transactional
    public TransferResult transfer(UUID transactionId, UUID sourceId, UUID destinationId, BigDecimal rawAmount) {
        var amount = money(rawAmount);
        if (sourceId.equals(destinationId) || amount.signum() <= 0) {
            throw new InvalidTransferException("accounts must differ and amount must be positive");
        }
        if (transfers.existsById(transactionId)) {
            return new TransferResult(transactionId, "COMPLETED", true);
        }

        var firstId = sourceId.compareTo(destinationId) < 0 ? sourceId : destinationId;
        var secondId = firstId.equals(sourceId) ? destinationId : sourceId;
        var first = accounts.findByIdForUpdate(firstId).orElseThrow(AccountNotFoundException::new);
        var second = accounts.findByIdForUpdate(secondId).orElseThrow(AccountNotFoundException::new);
        var source = first.getId().equals(sourceId) ? first : second;
        var destination = first.getId().equals(destinationId) ? first : second;

        source.debit(amount);
        destination.credit(amount);
        ledger.save(new LedgerEntry(transactionId, sourceId, amount.negate(), "TRANSFER_DEBIT"));
        ledger.save(new LedgerEntry(transactionId, destinationId, amount, "TRANSFER_CREDIT"));
        transfers.save(new TransferRecord(transactionId, sourceId, destinationId, amount));
        return new TransferResult(transactionId, "COMPLETED", false);
    }

    private static BigDecimal money(BigDecimal value) {
        if (value == null) throw new InvalidTransferException("amount is required");
        return value.setScale(2, RoundingMode.UNNECESSARY);
    }

    public record TransferResult(UUID transactionId, String status, boolean duplicate) {}
}
