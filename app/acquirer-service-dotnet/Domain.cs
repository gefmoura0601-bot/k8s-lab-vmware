using System.ComponentModel.DataAnnotations;

public sealed record CardInput(
    [property: Required, MaxLength(23)] string Number,
    [property: Required, MaxLength(120)] string HolderName,
    [property: Range(1, 12)] int ExpiryMonth,
    [property: Range(2026, 2200)] int ExpiryYear,
    [property: Required, RegularExpression("^[0-9]{3}$")] string Cvv);

public sealed record CreatePaymentRequest(
    [property: Required, MaxLength(40)] string MerchantId,
    [property: Required, MaxLength(120)] string MerchantName,
    Guid OrderId,
    decimal Amount,
    [property: Required, MaxLength(3)] string Currency,
    [property: Required, MaxLength(140)] string Description,
    [property: Required] CardInput Card,
    [property: Required, MaxLength(10)] string PaymentType,
    [property: Range(1, 12)] int Installments);

public sealed record Payment(
    Guid PaymentId,
    string MerchantId,
    Guid OrderId,
    decimal Amount,
    string Currency,
    string Description,
    string Last4,
    string PaymentType,
    string CardType,
    int Installments,
    string Status,
    string? AuthorizationCode,
    string? DeclineCode,
    DateTimeOffset CreatedAt,
    DateTimeOffset? CompletedAt);

public sealed record IssuerPaymentRequest(
    Guid PaymentId,
    string MerchantId,
    string MerchantName,
    string OrderReference,
    decimal Amount,
    string Currency,
    CardInput Card,
    string PaymentType,
    int Installments);

public sealed record IssuerPaymentResult(
    Guid PaymentId,
    string Status,
    string? AuthorizationCode,
    string? DeclineCode,
    string? CardType,
    string Last4);

public sealed record ApiError(string Code, string Message);

public sealed class PaymentConflictException : Exception;
public sealed class InvalidPaymentException(string message) : Exception(message);
public sealed class IssuerUnavailableException(string message) : Exception(message);

public interface IPaymentRepository
{
    Task<(Payment Payment, string RequestHash)> CreateOrGetAsync(
        Guid id, CreatePaymentRequest request, string normalizedPan, string requestHash,
        CancellationToken cancellationToken);
    Task<Payment?> GetAsync(Guid id, CancellationToken cancellationToken);
    Task<Payment> DecideAsync(Guid id, IssuerPaymentResult result, CancellationToken cancellationToken);
}

public interface IIssuerClient
{
    Task<IssuerPaymentResult> PayAsync(Guid id, CreatePaymentRequest request, string normalizedPan,
        CancellationToken cancellationToken);
}
