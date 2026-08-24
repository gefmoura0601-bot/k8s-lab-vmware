package com.banklab.accounts;
import java.time.Instant; import java.util.Map; import org.springframework.http.HttpStatus; import org.springframework.web.bind.MethodArgumentNotValidException; import org.springframework.web.bind.annotation.*;
@RestControllerAdvice class ApiExceptionHandler {
 @ExceptionHandler(AccountNotFoundException.class) @ResponseStatus(HttpStatus.NOT_FOUND) Map<String,Object> notFound(){return error("account_not_found","Account was not found");}
 @ExceptionHandler(PixKeyNotFoundException.class) @ResponseStatus(HttpStatus.NOT_FOUND) Map<String,Object> pixNotFound(){return error("pix_key_not_found","PIX key was not found");}
 @ExceptionHandler(CardNotFoundException.class) @ResponseStatus(HttpStatus.NOT_FOUND) Map<String,Object> cardNotFound(){return error("card_not_found","Card was not found");}
 @ExceptionHandler(TransferNotFoundException.class) @ResponseStatus(HttpStatus.NOT_FOUND) Map<String,Object> transferNotFound(){return error("transfer_not_found","Transfer was not found");}
 @ExceptionHandler(InsufficientFundsException.class) @ResponseStatus(HttpStatus.UNPROCESSABLE_ENTITY) Map<String,Object> funds(){return error("insufficient_funds","Insufficient funds");}
 @ExceptionHandler(InvalidTransferException.class) @ResponseStatus(HttpStatus.BAD_REQUEST) Map<String,Object> invalid(InvalidTransferException e){return error("invalid_request",e.getMessage());}
 @ExceptionHandler(MethodArgumentNotValidException.class) @ResponseStatus(HttpStatus.BAD_REQUEST) Map<String,Object> validation(){return error("invalid_request","Request validation failed");}
 @ExceptionHandler(UnauthorizedException.class) @ResponseStatus(HttpStatus.UNAUTHORIZED) Map<String,Object> unauthorized(){return error("unauthorized","Authentication is required");}
 @ExceptionHandler(InvalidCredentialsException.class) @ResponseStatus(HttpStatus.UNAUTHORIZED) Map<String,Object> credentials(){return error("invalid_credentials","Account or password is invalid");}
 @ExceptionHandler(AccountLockedException.class) @ResponseStatus(HttpStatus.TOO_MANY_REQUESTS) Map<String,Object> locked(){return error("account_locked","Account is temporarily locked");}
 @ExceptionHandler(IdempotencyConflictException.class) @ResponseStatus(HttpStatus.CONFLICT) Map<String,Object> conflict(){return error("idempotency_conflict","Idempotency key was already used for another operation");}
 @ExceptionHandler(InvalidCpfException.class) @ResponseStatus(HttpStatus.BAD_REQUEST) Map<String,Object> invalidCpf(){return error("invalid_cpf","CPF is invalid");}
 @ExceptionHandler(RegistrationConflictException.class) @ResponseStatus(HttpStatus.CONFLICT) Map<String,Object> registrationConflict(){return error("registration_conflict","Registration could not be completed");}
 private static Map<String,Object> error(String c,String m){return Map.of("code",c,"message",m,"timestamp",Instant.now().toString());}
}
class AccountNotFoundException extends RuntimeException{} class InsufficientFundsException extends RuntimeException{}
class PixKeyNotFoundException extends RuntimeException{} class TransferNotFoundException extends RuntimeException{}
class CardNotFoundException extends RuntimeException{} class CreditLineNotFoundException extends RuntimeException{}
class CreditLimitExceededException extends RuntimeException{}
class IdempotencyConflictException extends RuntimeException{}
class InvalidCpfException extends RuntimeException{} class RegistrationConflictException extends RuntimeException{}
class InvalidTransferException extends RuntimeException{InvalidTransferException(String m){super(m);}}
class UnauthorizedException extends RuntimeException{} class InvalidCredentialsException extends RuntimeException{} class AccountLockedException extends RuntimeException{}
