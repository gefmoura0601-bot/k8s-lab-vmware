package com.banklab.accounts;
import io.micrometer.core.instrument.MeterRegistry;
import org.springframework.stereotype.Component;
@Component
class LedgerMetrics {
    LedgerMetrics(MeterRegistry registry, LedgerEntryRepository ledger) {
        registry.gauge("banking.ledger.divergent.accounts", ledger, LedgerEntryRepository::countDivergentAccounts);
    }
}
