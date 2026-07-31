package com.banklab.accounts;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.when;

import java.math.BigDecimal;
import java.util.Optional;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

@ExtendWith(MockitoExtension.class)
class AccountServiceTest {
    @Mock AccountRepository accounts;
    @Mock TransferRecordRepository transfers;
    @InjectMocks AccountService service;

    @Test
    void transfersMoneyAtomically() {
        var transactionId = UUID.randomUUID();
        var source = new Account(UUID.randomUUID(), "Alice", new BigDecimal("100.00"));
        var destination = new Account(UUID.randomUUID(), "Bob", new BigDecimal("20.00"));
        var first = source.getId().compareTo(destination.getId()) < 0 ? source : destination;
        var second = first == source ? destination : source;
        when(accounts.findByIdForUpdate(first.getId())).thenReturn(Optional.of(first));
        when(accounts.findByIdForUpdate(second.getId())).thenReturn(Optional.of(second));

        var result = service.transfer(transactionId, source.getId(), destination.getId(), new BigDecimal("25.00"));

        assertThat(result.status()).isEqualTo("COMPLETED");
        assertThat(source.getBalance()).isEqualByComparingTo("75.00");
        assertThat(destination.getBalance()).isEqualByComparingTo("45.00");
    }

    @Test
    void rejectsInsufficientFunds() {
        var source = new Account(UUID.randomUUID(), "Alice", new BigDecimal("10.00"));
        var destination = new Account(UUID.randomUUID(), "Bob", BigDecimal.ZERO.setScale(2));
        var first = source.getId().compareTo(destination.getId()) < 0 ? source : destination;
        var second = first == source ? destination : source;
        when(accounts.findByIdForUpdate(first.getId())).thenReturn(Optional.of(first));
        when(accounts.findByIdForUpdate(second.getId())).thenReturn(Optional.of(second));

        assertThatThrownBy(() -> service.transfer(
            UUID.randomUUID(), source.getId(), destination.getId(), new BigDecimal("11.00")
        )).isInstanceOf(InsufficientFundsException.class);
    }
}
