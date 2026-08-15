package com.banklab.accounts;

import jakarta.persistence.LockModeType;
import java.util.Optional;
import java.util.UUID;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

interface AccountRepository extends JpaRepository<Account, UUID> {
    Optional<Account> findByAccountNumber(String accountNumber);
    boolean existsByAccountNumber(String accountNumber);
    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select a from Account a where a.id = :id")
    Optional<Account> findByIdForUpdate(@Param("id") UUID id);
}

interface TransferRecordRepository extends JpaRepository<TransferRecord, UUID> {
    Optional<TransferRecord> findByReversalOf(UUID transactionId);
}
interface PixKeyRepository extends JpaRepository<PixKey, UUID> {
    Optional<PixKey> findByAccountId(UUID accountId);
}
interface LedgerEntryRepository extends JpaRepository<LedgerEntry, Long> {
    @Query(value = "SELECT count(*) FROM account_service.accounts a WHERE a.balance <> COALESCE((SELECT sum(l.signed_amount) FROM account_service.ledger_entries l WHERE l.account_id = a.id), 0)", nativeQuery = true)
    long countDivergentAccounts();
}
