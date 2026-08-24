package com.banklab.accounts;

import jakarta.persistence.LockModeType;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

interface AccountRepository extends JpaRepository<Account, UUID> {
    @Lock(LockModeType.PESSIMISTIC_WRITE)
    Optional<Account> findByAccountNumber(String accountNumber);
    boolean existsByAccountNumber(String accountNumber);
    boolean existsByCpfFingerprint(String cpfFingerprint);
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

interface CardRepository extends JpaRepository<Card, UUID> {
    Optional<Card> findByAccountIdAndCardTypeAndFormFactor(
        UUID accountId, CardType cardType, CardFormFactor formFactor);
    List<Card> findAllByAccountIdOrderByCreatedAtDesc(UUID accountId);
    boolean existsByPanFingerprint(String panFingerprint);
    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select c from Card c where c.panFingerprint = :panFingerprint")
    Optional<Card> findByPanFingerprintForUpdate(@Param("panFingerprint") String panFingerprint);
}

interface CardCreditLineRepository extends JpaRepository<CardCreditLine, UUID> {
    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select c from CardCreditLine c where c.accountId = :accountId")
    Optional<CardCreditLine> findByIdForUpdate(@Param("accountId") UUID accountId);
}

interface CardPaymentRepository extends JpaRepository<CardPayment, UUID> {
    List<CardPayment> findAllByAccountIdOrderByCreatedAtDesc(UUID accountId);

    @Query(value = "SELECT 1 FROM pg_advisory_xact_lock(hashtextextended(CAST(:paymentId AS text), 0))",
        nativeQuery = true)
    int acquirePaymentLock(@Param("paymentId") UUID paymentId);
}
