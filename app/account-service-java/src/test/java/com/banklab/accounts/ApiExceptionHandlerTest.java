package com.banklab.accounts;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;

class ApiExceptionHandlerTest {
    private final ApiExceptionHandler handler = new ApiExceptionHandler();

    @Test
    void returnsStableValidationMessageWithoutRejectedValue() {
        assertThat(handler.validation())
            .containsEntry("code", "invalid_request")
            .containsEntry("message", "Request validation failed");
    }

    @Test
    void keepsRegistrationConflictDeliberatelyGeneric() {
        assertThat(handler.registrationConflict())
            .containsEntry("code", "registration_conflict")
            .containsEntry("message", "Registration could not be completed");
    }
}
