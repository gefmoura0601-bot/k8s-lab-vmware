using System.ComponentModel.DataAnnotations;

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
    Task<Transaction> CreateOrGetAsync(
        Guid id,
        TransferRequest request,
        CancellationToken cancellationToken);
    Task<Transaction> CompleteAsync(Guid id, CancellationToken cancellationToken);
    Task<Transaction> FailAsync(Guid id, string failureCode, CancellationToken cancellationToken);
}

public interface IAccountClient
{
    Task ApplyTransferAsync(Guid transactionId, TransferRequest request, CancellationToken cancellationToken);
}
