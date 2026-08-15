package com.banklab.accounts;

import jakarta.validation.Valid;
import jakarta.validation.constraints.*;
import java.math.BigDecimal;
import java.time.Duration;
import java.util.List;
import java.util.UUID;
import org.springframework.http.*;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/v1")
class AccountController {
    private final AuthService auth;
    private final AccountService accounts;
    AccountController(AuthService auth, AccountService accounts) { this.auth = auth; this.accounts = accounts; }

    @PostMapping("/auth/register")
    ResponseEntity<AccountResponse> register(@Valid @RequestBody RegisterRequest request) {
        var account = auth.register(request.ownerName(), request.password(), BigDecimal.ZERO);
        var token = auth.login(account.getAccountNumber(), request.password());
        return ResponseEntity.status(HttpStatus.CREATED).header(HttpHeaders.SET_COOKIE, session(token).toString())
            .body(AccountResponse.from(account));
    }

    @PostMapping("/auth/login")
    ResponseEntity<AccountResponse> login(@Valid @RequestBody LoginRequest request) {
        var token = auth.login(request.accountNumber(), request.password());
        return ResponseEntity.ok().header(HttpHeaders.SET_COOKIE, session(token).toString())
            .body(AccountResponse.from(auth.authenticate(token)));
    }

    @PostMapping("/auth/logout")
    ResponseEntity<Void> logout() {
        var cookie = ResponseCookie.from(AuthService.COOKIE_NAME, "").httpOnly(true).secure(true)
            .sameSite("Strict").path("/").maxAge(Duration.ZERO).build();
        return ResponseEntity.noContent().header(HttpHeaders.SET_COOKIE, cookie.toString()).build();
    }

    @GetMapping("/accounts/me")
    AccountResponse me(@CookieValue(name = AuthService.COOKIE_NAME, defaultValue = "") String token) {
        return AccountResponse.from(auth.authenticate(token));
    }

    @GetMapping("/accounts/directory")
    List<DirectoryEntry> directory(@CookieValue(name = AuthService.COOKIE_NAME, defaultValue = "") String token) {
        auth.authenticate(token);
        return accounts.directory().stream().map(DirectoryEntry::from).toList();
    }

    private static ResponseCookie session(String token) {
        return ResponseCookie.from(AuthService.COOKIE_NAME, token).httpOnly(true).secure(true)
            .sameSite("Strict").path("/").maxAge(Duration.ofMinutes(15)).build();
    }
    record RegisterRequest(@NotBlank @Size(max=120) String ownerName,
        @NotBlank @Size(min=10,max=72) String password) {}
    record LoginRequest(@NotBlank String accountNumber, @NotBlank String password) {}
    record AccountResponse(UUID id, String accountNumber, String ownerName, BigDecimal balance) {
        static AccountResponse from(Account a) { return new AccountResponse(a.getId(),a.getAccountNumber(),a.getOwnerName(),a.getBalance()); }
    }
    record DirectoryEntry(UUID id, String accountNumber, String ownerName) {
        static DirectoryEntry from(Account a) { return new DirectoryEntry(a.getId(),a.getAccountNumber(),a.getOwnerName()); }
    }
}

@RestController
@RequestMapping("/internal/v1")
class InternalController {
    private final AccountService accounts;
    private final AuthService auth;
    InternalController(AccountService accounts, AuthService auth) { this.accounts=accounts; this.auth=auth; }

    @PostMapping("/auth/authorize")
    void authorize(@CookieValue(name=AuthService.COOKIE_NAME,defaultValue="") String token,
                   @RequestBody AuthorizeRequest request) {
        if (!auth.authenticate(token).getId().equals(request.sourceAccountId())) throw new UnauthorizedException();
    }

    @PostMapping("/auth/confirm")
    void confirm(@CookieValue(name=AuthService.COOKIE_NAME,defaultValue="") String token,
                 @RequestBody ConfirmRequest request) {
        auth.confirm(token, request.sourceAccountId(), request.password());
    }

    @PostMapping("/transfers")
    AccountService.TransferResult transfer(@Valid @RequestBody TransferRequest r) {
        return accounts.transfer(r.transactionId(),r.sourceAccountId(),r.destinationAccountId(),r.amount());
    }
    record AuthorizeRequest(UUID sourceAccountId) {}
    record ConfirmRequest(UUID sourceAccountId, String password) {}
    record TransferRequest(@NotNull UUID transactionId,@NotNull UUID sourceAccountId,
        @NotNull UUID destinationAccountId,@NotNull @DecimalMin("0.01") BigDecimal amount) {}
}
