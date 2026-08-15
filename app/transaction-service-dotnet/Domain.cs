using System.ComponentModel.DataAnnotations;

public record CreateTransactionRequest(
    Guid SourceAccountId,
    Guid DestinationAccountId,
    decimal Amount,
    [property: Required, MaxLength(140)] string Description,
    [property: Required, MinLength(10), MaxLength(72)] string Password);

public record TransferRequest(
    Guid SourceAccountId,
    Guid DestinationAccountId,
    decimal Amount,
    [property: Required, MaxLength(140)] string Description);

public record Transaction(
    Guid Id,
    Guid SourceAccountId,
    Guid DestinationAccountId,
    decimal Amount,
    string Description,
    TransactionStatus Status,
    DateTimeOffset CreatedAt,
    DateTimeOffset? CompletedAt,
    string? FailureCode);

public enum TransactionStatus
{
    Pending,
    Completed,
    Failed
}

public record ApiError(string Code, string Message);

public interface ITransactionRepository
{
    Task<Transaction?> GetAsync(Guid id, CancellationToken cancellationToken);
    Task<IReadOnlyList<Transaction>> ListBySourceAsync(Guid sourceAccountId, CancellationToken cancellationToken);
    Task<Transaction> CreateOrGetAsync(
        Guid id,
        TransferRequest request,
        CancellationToken cancellationToken);
    Task<Transaction> CompleteAsync(Guid id, CancellationToken cancellationToken);
    Task<Transaction> FailAsync(Guid id, string failureCode, CancellationToken cancellationToken);
}

public interface IAccountClient
{
    Task AuthorizeAsync(Guid sourceAccountId, string sessionCookie, CancellationToken cancellationToken);
    Task ConfirmAsync(Guid sourceAccountId, string password, string sessionCookie, CancellationToken cancellationToken);
    Task ApplyTransferAsync(Guid transactionId, TransferRequest request, CancellationToken cancellationToken);
}
