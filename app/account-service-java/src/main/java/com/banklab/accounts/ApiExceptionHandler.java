package com.banklab.accounts;
import java.time.Instant; import java.util.Map; import org.springframework.http.HttpStatus; import org.springframework.web.bind.MethodArgumentNotValidException; import org.springframework.web.bind.annotation.*;
@RestControllerAdvice class ApiExceptionHandler {
 @ExceptionHandler(AccountNotFoundException.class) @ResponseStatus(HttpStatus.NOT_FOUND) Map<String,Object> notFound(){return error("account_not_found","Account was not found");}
 @ExceptionHandler(InsufficientFundsException.class) @ResponseStatus(HttpStatus.UNPROCESSABLE_ENTITY) Map<String,Object> funds(){return error("insufficient_funds","Insufficient funds");}
 @ExceptionHandler({InvalidTransferException.class,MethodArgumentNotValidException.class}) @ResponseStatus(HttpStatus.BAD_REQUEST) Map<String,Object> invalid(Exception e){return error("invalid_request",e.getMessage());}
 @ExceptionHandler(UnauthorizedException.class) @ResponseStatus(HttpStatus.UNAUTHORIZED) Map<String,Object> unauthorized(){return error("unauthorized","Authentication is required");}
 @ExceptionHandler(InvalidCredentialsException.class) @ResponseStatus(HttpStatus.UNAUTHORIZED) Map<String,Object> credentials(){return error("invalid_credentials","Account or password is invalid");}
 @ExceptionHandler(AccountLockedException.class) @ResponseStatus(HttpStatus.TOO_MANY_REQUESTS) Map<String,Object> locked(){return error("account_locked","Account is temporarily locked");}
 private static Map<String,Object> error(String c,String m){return Map.of("code",c,"message",m,"timestamp",Instant.now().toString());}
}
class AccountNotFoundException extends RuntimeException{} class InsufficientFundsException extends RuntimeException{}
class InvalidTransferException extends RuntimeException{InvalidTransferException(String m){super(m);}}
class UnauthorizedException extends RuntimeException{} class InvalidCredentialsException extends RuntimeException{} class AccountLockedException extends RuntimeException{}
