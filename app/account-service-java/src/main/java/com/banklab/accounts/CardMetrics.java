package com.banklab.accounts;

import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import java.util.Locale;
import org.springframework.stereotype.Component;

@Component
public class CardMetrics {
    private final MeterRegistry registry;

    CardMetrics(MeterRegistry registry) { this.registry = registry; }

    void issued(CardType type) {
        counter("banking.cards.issued", "Virtual cards issued", type, "issued").increment();
    }

    void revealed(CardType type) {
        counter("banking.cards.revealed", "Virtual card credentials revealed", type, "revealed").increment();
    }

    void paymentCaptured(CardType type) {
        counter("banking.card.payments", "Card payments grouped by final outcome", type, "captured").increment();
    }

    void paymentDeclined(CardType type) {
        counter("banking.card.payments", "Card payments grouped by final outcome", type, "declined").increment();
    }

    private Counter counter(String name, String description, CardType type, String outcome) {
        return Counter.builder(name)
            .description(description)
            .tag("card_type", type.name().toLowerCase(Locale.ROOT))
            .tag("outcome", outcome)
            .register(registry);
    }
}
