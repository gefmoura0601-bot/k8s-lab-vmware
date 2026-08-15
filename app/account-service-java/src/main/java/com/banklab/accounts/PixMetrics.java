package com.banklab.accounts;

import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import org.springframework.stereotype.Component;

@Component
class PixMetrics {
    private final Counter keysCreated;
    private final Counter keysResolved;
    private final Counter reversalsCompleted;

    PixMetrics(MeterRegistry registry) {
        keysCreated = registry.counter("banking.pix.keys.created");
        keysResolved = registry.counter("banking.pix.keys.resolved");
        reversalsCompleted = registry.counter("banking.pix.reversals.completed");
    }

    void keyCreated() { keysCreated.increment(); }
    void keyResolved() { keysResolved.increment(); }
    void reversalCompleted() { reversalsCompleted.increment(); }
}
