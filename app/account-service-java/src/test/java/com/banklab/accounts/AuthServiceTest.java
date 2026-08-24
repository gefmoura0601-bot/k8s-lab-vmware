package com.banklab.accounts;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.Optional;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.transaction.annotation.Transactional;

@ExtendWith(MockitoExtension.class)
class AuthServiceTest {
    private static final String SESSION_SECRET = "unit-test-banking-session-secret-with-32-characters";
    private static final String IDENTITY_SECRET = "unit-test-banking-identity-secret-with-32-characters";
    @Mock AccountRepository accounts;
    @Mock AccountService accountService;
    private CpfService cpfs;
    private AuthService auth;

    @BeforeEach
    void setUp() {
        cpfs = new CpfService(IDENTITY_SECRET);
        auth = new AuthService(accounts, accountService, cpfs, SESSION_SECRET);
    }

    @Test
    void registersOnlyCpfFingerprintAndSuffix() {
        var identity = cpfs.identity("529.982.247-25");
        var created = new Account(UUID.randomUUID(), "12345678", "Alice", "hash",
            BigDecimal.ZERO.setScale(2), identity.fingerprint(), identity.last4());
        when(accountService.create(anyString(), eq("Alice"), anyString(), eq(BigDecimal.ZERO),
            eq(identity.fingerprint()), eq(identity.last4()))).thenReturn(created);

        var result = auth.register("Alice", "529.982.247-25", "StrongPass123", BigDecimal.ZERO);

        assertThat(result.getCpfLast4()).isEqualTo("4725");
        assertThat(AccountController.AccountResponse.from(result).cpfMasked())
            .isEqualTo("***.***.*47-25");
        verify(accounts).existsByCpfFingerprint(identity.fingerprint());
    }

    @Test
    void rejectsCpfAlreadyRegisteredWithoutCreatingAnotherAccount() {
        var identity = cpfs.identity("529.982.247-25");
        when(accounts.existsByCpfFingerprint(identity.fingerprint())).thenReturn(true);

        assertThatThrownBy(() -> auth.register(
            "Alice", "52998224725", "StrongPass123", BigDecimal.ZERO))
            .isInstanceOf(RegistrationConflictException.class);
        verifyNoInteractions(accountService);
    }

    @Test
    void mapsConcurrentUniqueViolationToStableRegistrationConflict() {
        when(accountService.create(anyString(), eq("Alice"), anyString(), eq(BigDecimal.ZERO),
            anyString(), eq("4725"))).thenThrow(new DataIntegrityViolationException("unique"));

        assertThatThrownBy(() -> auth.register(
            "Alice", "52998224725", "StrongPass123", BigDecimal.ZERO))
            .isInstanceOf(RegistrationConflictException.class)
            .hasMessage(null);
    }

    @Test
    void rejectsInvalidCpfBeforePersistence() {
        assertThatThrownBy(() -> auth.register(
            "Alice", "111.111.111-11", "StrongPass123", BigDecimal.ZERO))
            .isInstanceOf(InvalidCpfException.class);
        verifyNoInteractions(accounts, accountService);
    }

    @Test
    void locksAccountAfterFiveWrongPasswords() {
        var account = new Account(UUID.randomUUID(), "12345678", "Alice",
            new BCryptPasswordEncoder(4).encode("CorrectPass123"), BigDecimal.ZERO.setScale(2));
        when(accounts.findByAccountNumber("12345678")).thenReturn(Optional.of(account));

        for (var attempt = 0; attempt < 5; attempt++) {
            assertThatThrownBy(() -> auth.login("12345678", "WrongPass123"))
                .isInstanceOf(InvalidCredentialsException.class);
        }

        assertThat(account.isLocked(Instant.now())).isTrue();
        assertThatThrownBy(() -> auth.login("12345678", "CorrectPass123"))
            .isInstanceOf(AccountLockedException.class);
    }

    @Test
    void authenticationFailuresCommitLockoutState() throws Exception {
        var login = AuthService.class.getDeclaredMethod("login", String.class, String.class)
            .getAnnotation(Transactional.class);
        var confirm = AuthService.class.getDeclaredMethod(
            "confirm", String.class, UUID.class, String.class).getAnnotation(Transactional.class);

        assertThat(login.noRollbackFor()).contains(InvalidCredentialsException.class);
        assertThat(confirm.noRollbackFor()).contains(InvalidCredentialsException.class);
    }
}
