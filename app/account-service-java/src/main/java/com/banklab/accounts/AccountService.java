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
    private final PixKeyRepository pixKeys;
    private final PixMetrics pixMetrics;

    AccountService(AccountRepository accounts, TransferRecordRepository transfers,
                   LedgerEntryRepository ledger, PixKeyRepository pixKeys, PixMetrics pixMetrics) {
        this.accounts = accounts;
        this.transfers = transfers;
        this.ledger = ledger;
        this.pixKeys = pixKeys;
        this.pixMetrics = pixMetrics;
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
        return create(number, owner, passwordHash, initialBalance, null, null);
    }

    @Transactional
    public Account create(String number, String owner, String passwordHash, BigDecimal initialBalance,
                          String cpfFingerprint, String cpfLast4) {
        var balance = money(initialBalance);
        if (balance.signum() < 0) throw new InvalidTransferException("initialBalance must not be negative");
        var account = accounts.saveAndFlush(new Account(
            UUID.randomUUID(), number, owner.trim(), passwordHash, balance, cpfFingerprint, cpfLast4));
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
    public PixKey getOrCreatePixKey(UUID accountId) {
        accounts.findById(accountId).orElseThrow(AccountNotFoundException::new);
        return pixKeys.findByAccountId(accountId)
            .orElseGet(() -> {
                var key = pixKeys.save(new PixKey(UUID.randomUUID(), accountId));
                pixMetrics.keyCreated();
                return key;
            });
    }

    @Transactional(readOnly = true)
    public PixKey getPixKey(UUID accountId) {
        return pixKeys.findByAccountId(accountId).orElseThrow(PixKeyNotFoundException::new);
    }

    @Transactional(readOnly = true)
    public Account resolvePixKey(UUID pixKey) {
        var key = pixKeys.findById(pixKey).orElseThrow(PixKeyNotFoundException::new);
        var account = accounts.findById(key.getAccountId()).orElseThrow(AccountNotFoundException::new);
        pixMetrics.keyResolved();
        return account;
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

    @Transactional
    public TransferResult reverse(UUID reversalId, UUID originalId) {
        var existing = transfers.findById(reversalId);
        if (existing.isPresent()) {
            if (!originalId.equals(existing.get().getReversalOf())) throw new IdempotencyConflictException();
            return new TransferResult(reversalId, "COMPLETED", true);
        }
        var prior = transfers.findByReversalOf(originalId);
        if (prior.isPresent()) return new TransferResult(prior.get().getTransactionId(), "COMPLETED", true);
        var original = transfers.findById(originalId).orElseThrow(TransferNotFoundException::new);
        if (original.getReversalOf() != null) throw new InvalidTransferException("a reversal cannot be reversed");
        return reverseLocked(reversalId, originalId, original);
    }

    private TransferResult reverseLocked(UUID reversalId, UUID originalId, TransferRecord original) {
        var sourceId = original.getDestinationAccountId();
        var destinationId = original.getSourceAccountId();
        var firstId = sourceId.compareTo(destinationId) < 0 ? sourceId : destinationId;
        var secondId = firstId.equals(sourceId) ? destinationId : sourceId;
        var first = accounts.findByIdForUpdate(firstId).orElseThrow(AccountNotFoundException::new);
        var second = accounts.findByIdForUpdate(secondId).orElseThrow(AccountNotFoundException::new);
        return completeReversal(reversalId, originalId, original, sourceId, destinationId, first, second);
    }

    private TransferResult completeReversal(UUID reversalId, UUID originalId, TransferRecord original,
                                            UUID sourceId, UUID destinationId, Account first, Account second) {
        var source = first.getId().equals(sourceId) ? first : second;
        var destination = first.getId().equals(destinationId) ? first : second;
        source.debit(original.getAmount());
        destination.credit(original.getAmount());
        return saveReversal(reversalId, originalId, original, sourceId, destinationId);
    }

    private TransferResult saveReversal(UUID reversalId, UUID originalId, TransferRecord original,
                                        UUID sourceId, UUID destinationId) {
        ledger.save(new LedgerEntry(reversalId, sourceId, original.getAmount().negate(), "REVERSAL_DEBIT", originalId));
        ledger.save(new LedgerEntry(reversalId, destinationId, original.getAmount(), "REVERSAL_CREDIT", originalId));
        transfers.save(new TransferRecord(reversalId, sourceId, destinationId, original.getAmount(), originalId));
        pixMetrics.reversalCompleted();
        return new TransferResult(reversalId, "COMPLETED", false);
    }

    private static BigDecimal money(BigDecimal value) {
        if (value == null) throw new InvalidTransferException("amount is required");
        return value.setScale(2, RoundingMode.UNNECESSARY);
    }

    public record TransferResult(UUID transactionId, String status, boolean duplicate) {}
}
