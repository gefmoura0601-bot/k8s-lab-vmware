package com.banklab.accounts;

import java.time.Instant;
import java.util.Map;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestControllerAdvice;

@RestControllerAdvice
class ApiExceptionHandler {
    @ExceptionHandler(AccountNotFoundException.class)
    @ResponseStatus(HttpStatus.NOT_FOUND)
    Map<String, Object> notFound() { return error("account_not_found", "Account was not found"); }

    @ExceptionHandler(InsufficientFundsException.class)
    @ResponseStatus(HttpStatus.UNPROCESSABLE_ENTITY)
    Map<String, Object> insufficientFunds() { return error("insufficient_funds", "Insufficient funds"); }

    @ExceptionHandler({InvalidTransferException.class, MethodArgumentNotValidException.class})
    @ResponseStatus(HttpStatus.BAD_REQUEST)
    Map<String, Object> invalid(Exception exception) { return error("invalid_request", exception.getMessage()); }

    private static Map<String, Object> error(String code, String message) {
        return Map.of("code", code, "message", message, "timestamp", Instant.now().toString());
    }
}

class AccountNotFoundException extends RuntimeException {}
class InsufficientFundsException extends RuntimeException {}
class InvalidTransferException extends RuntimeException {
    InvalidTransferException(String message) { super(message); }
}
