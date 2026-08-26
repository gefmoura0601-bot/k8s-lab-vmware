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
    private final CardService cards;
    AccountController(AuthService auth, AccountService accounts, CardService cards) {
        this.auth = auth;
        this.accounts = accounts;
        this.cards = cards;
    }

    @PostMapping("/auth/register")
    ResponseEntity<AccountResponse> register(@Valid @RequestBody RegisterRequest request) {
        var account = auth.register(request.ownerName(), request.cpf(), request.password(), BigDecimal.ZERO);
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
            .sameSite("Strict").path("/bank").maxAge(Duration.ZERO).build();
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

    @GetMapping("/accounts/me/pix-key")
    PixKeyResponse pixKey(@CookieValue(name = AuthService.COOKIE_NAME, defaultValue = "") String token) {
        return PixKeyResponse.from(accounts.getPixKey(auth.authenticate(token).getId()));
    }

    @PutMapping("/accounts/me/pix-key")
    PixKeyResponse createPixKey(@CookieValue(name = AuthService.COOKIE_NAME, defaultValue = "") String token) {
        return PixKeyResponse.from(accounts.getOrCreatePixKey(auth.authenticate(token).getId()));
    }

    @PostMapping("/accounts/me/cards")
    ResponseEntity<CardService.CardDetails> createCard(
        @CookieValue(name = AuthService.COOKIE_NAME, defaultValue = "") String token,
        @Valid @RequestBody IssueCardRequest request) {
        var account = auth.authenticate(token);
        auth.confirm(token, account.getId(), request.password());
        return ResponseEntity.status(HttpStatus.CREATED)
            .cacheControl(CacheControl.noStore())
            .body(cards.issue(account.getId(), request.type()));
    }

    @GetMapping("/accounts/me/cards")
    List<CardService.CardSummary> cards(
        @CookieValue(name = AuthService.COOKIE_NAME, defaultValue = "") String token) {
        return cards.list(auth.authenticate(token).getId());
    }

    @PostMapping("/accounts/me/cards/{cardId}/reveal")
    ResponseEntity<CardService.CardDetails> revealCard(
        @CookieValue(name = AuthService.COOKIE_NAME, defaultValue = "") String token,
        @PathVariable UUID cardId,
        @Valid @RequestBody ConfirmPasswordRequest request) {
        var account = auth.authenticate(token);
        auth.confirm(token, account.getId(), request.password());
        return ResponseEntity.ok()
            .cacheControl(CacheControl.noStore())
            .body(cards.reveal(account.getId(), cardId));
    }

    @GetMapping("/accounts/me/card-purchases")
    List<CardService.CardPurchaseView> cardPurchases(
        @CookieValue(name = AuthService.COOKIE_NAME, defaultValue = "") String token) {
        return cards.purchases(auth.authenticate(token).getId());
    }

    private static ResponseCookie session(String token) {
        return ResponseCookie.from(AuthService.COOKIE_NAME, token).httpOnly(true).secure(true)
            .sameSite("Strict").path("/bank").maxAge(Duration.ofMinutes(15)).build();
    }
    record RegisterRequest(@NotBlank @Size(max=120) String ownerName,
        @NotBlank @Size(max=20) String cpf,
        @NotBlank @Size(min=10,max=72) String password) {}
    record LoginRequest(@NotBlank String accountNumber, @NotBlank String password) {}
    record IssueCardRequest(@NotNull CardType type,
        @NotBlank @Size(min=10,max=72) String password) {}
    record ConfirmPasswordRequest(@NotBlank @Size(min=10,max=72) String password) {}
    record AccountResponse(UUID id, String accountNumber, String ownerName, BigDecimal balance,
                           String cpfMasked) {
        static AccountResponse from(Account a) { return new AccountResponse(a.getId(),a.getAccountNumber(),a.getOwnerName(),a.getBalance(),CpfService.masked(a.getCpfLast4())); }
    }
    record DirectoryEntry(UUID id, String accountNumber, String ownerName) {
        static DirectoryEntry from(Account a) { return new DirectoryEntry(a.getId(),a.getAccountNumber(),a.getOwnerName()); }
    }
    record PixKeyResponse(UUID pixKey, UUID accountId) {
        static PixKeyResponse from(PixKey key) { return new PixKeyResponse(key.getPixKey(), key.getAccountId()); }
    }
}

@RestController
@RequestMapping("/internal/v1")
class InternalController {
    private final AccountService accounts;
    private final AuthService auth;
    private final CardService cards;
    InternalController(AccountService accounts, AuthService auth, CardService cards) {
        this.accounts=accounts;
        this.auth=auth;
        this.cards=cards;
    }

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

    @GetMapping("/pix-keys/{pixKey}")
    AccountController.DirectoryEntry resolvePixKey(@PathVariable UUID pixKey) {
        return AccountController.DirectoryEntry.from(accounts.resolvePixKey(pixKey));
    }

    @PostMapping("/transfers/{transactionId}/reversals")
    AccountService.TransferResult reverse(@PathVariable UUID transactionId,
                                           @Valid @RequestBody ReversalRequest request) {
        return accounts.reverse(request.reversalId(), transactionId);
    }

    @PostMapping("/card-payments")
    CardService.PaymentResult cardPayment(@Valid @RequestBody CardService.CardPaymentRequest request) {
        return cards.authorizeAndCapture(request);
    }
    record AuthorizeRequest(UUID sourceAccountId) {}
    record ConfirmRequest(UUID sourceAccountId, String password) {}
    record TransferRequest(@NotNull UUID transactionId,@NotNull UUID sourceAccountId,
        @NotNull UUID destinationAccountId,@NotNull @DecimalMin("0.01") BigDecimal amount) {}
    record ReversalRequest(@NotNull UUID reversalId) {}
}
