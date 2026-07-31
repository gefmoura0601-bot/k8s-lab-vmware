package com.banklab.accounts;

import jakarta.validation.Valid;
import jakarta.validation.constraints.DecimalMin;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;
import java.math.BigDecimal;
import java.util.List;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/accounts")
class AccountController {
    private final AccountService service;

    AccountController(AccountService service) { this.service = service; }

    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    AccountResponse create(@Valid @RequestBody CreateAccountRequest request) {
        return AccountResponse.from(service.create(request.ownerName(), request.initialBalance()));
    }

    @GetMapping("/{id}")
    AccountResponse get(@PathVariable UUID id) {
        return AccountResponse.from(service.get(id));
    }

    @GetMapping
    List<AccountResponse> list() {
        return service.list().stream().map(AccountResponse::from).toList();
    }

    record CreateAccountRequest(
        @NotBlank @Size(max = 120) String ownerName,
        @NotNull @DecimalMin("0.00") BigDecimal initialBalance
    ) {}

    record AccountResponse(UUID id, String ownerName, BigDecimal balance) {
        static AccountResponse from(Account account) {
            return new AccountResponse(account.getId(), account.getOwnerName(), account.getBalance());
        }
    }
}

@RestController
@RequestMapping("/internal/v1/transfers")
class InternalTransferController {
    private final AccountService service;

    InternalTransferController(AccountService service) { this.service = service; }

    @PostMapping
    AccountService.TransferResult transfer(@Valid @RequestBody TransferRequest request) {
        return service.transfer(
            request.transactionId(), request.sourceAccountId(), request.destinationAccountId(), request.amount()
        );
    }

    record TransferRequest(
        @NotNull UUID transactionId,
        @NotNull UUID sourceAccountId,
        @NotNull UUID destinationAccountId,
        @NotNull @DecimalMin(value = "0.01") BigDecimal amount
    ) {}
}
