using System.Security.Cryptography;
using System.Text;

public sealed class PaymentCoordinator(
    IPaymentRepository repository,
    IIssuerClient issuer,
    PaymentFingerprint fingerprint)
{
    public async Task<Payment> ExecuteAsync(Guid id, CreatePaymentRequest request, CancellationToken cancellationToken)
    {
        var pan = Validate(request);
        var hash = fingerprint.Compute(request, pan);
        var current = await repository.CreateOrGetAsync(id, request, pan, hash, cancellationToken);
        if (!CryptographicOperations.FixedTimeEquals(
                Encoding.ASCII.GetBytes(current.RequestHash), Encoding.ASCII.GetBytes(hash)))
            throw new PaymentConflictException();
        if (current.Payment.Status is "CAPTURED" or "DECLINED") return current.Payment;
        var decision = await issuer.PayAsync(id, request, pan, cancellationToken);
        if (!ValidDecision(id, request.PaymentType.Trim().ToUpperInvariant(), pan, decision))
            throw new IssuerUnavailableException("Issuer returned an invalid status");
        return await repository.DecideAsync(id, decision, cancellationToken);
    }

    public static string Validate(CreatePaymentRequest request)
    {
        if (request is null || request.Card is null)
            throw new InvalidPaymentException("Payment data is invalid");
        if (string.IsNullOrWhiteSpace(request.Card.Number) || request.Card.Number.Length > 23)
            throw new InvalidPaymentException("Card data is invalid");
        if (request.Card.Number.Any(character =>
                !char.IsAsciiDigit(character) && character != ' ' && character != '-'))
            throw new InvalidPaymentException("Card data is invalid");
        var pan = new string(request.Card.Number.Where(char.IsAsciiDigit).ToArray());
        if (pan.Length != 16 || !pan.StartsWith("999999", StringComparison.Ordinal) || !Luhn(pan))
            throw new InvalidPaymentException("Only Moura laboratory cards are accepted");
        if (request.OrderId == Guid.Empty || request.Amount <= 0
            || request.Amount > 99_999_999_999_999_999.99m
            || decimal.Round(request.Amount, 2) != request.Amount)
            throw new InvalidPaymentException("Order and amount are invalid");
        if (string.IsNullOrWhiteSpace(request.Currency) || request.Currency.Length != 3
            || !string.Equals(request.Currency, "BRL", StringComparison.OrdinalIgnoreCase))
            throw new InvalidPaymentException("Only BRL is supported");
        if (string.IsNullOrWhiteSpace(request.PaymentType))
            throw new InvalidPaymentException("Payment type is invalid");
        if (string.IsNullOrWhiteSpace(request.MerchantId) || request.MerchantId.Trim().Length > 40
            || string.IsNullOrWhiteSpace(request.MerchantName) || request.MerchantName.Trim().Length > 120
            || string.IsNullOrWhiteSpace(request.Description) || request.Description.Trim().Length > 140)
            throw new InvalidPaymentException("Merchant and description are invalid");
        if (string.IsNullOrWhiteSpace(request.Card.HolderName)
            || request.Card.HolderName.Trim().Length > 120
            || request.Card.ExpiryMonth is < 1 or > 12
            || request.Card.ExpiryYear is < 2026 or > 9999
            || string.IsNullOrWhiteSpace(request.Card.Cvv)
            || request.Card.Cvv.Length != 3
            || request.Card.Cvv.Any(character => !char.IsAsciiDigit(character)))
            throw new InvalidPaymentException("Card data is invalid");
        var now = DateTime.UtcNow;
        if (request.Card.ExpiryYear < now.Year
            || (request.Card.ExpiryYear == now.Year && request.Card.ExpiryMonth < now.Month))
            throw new InvalidPaymentException("Card data is invalid");
        var type = request.PaymentType.Trim().ToUpperInvariant();
        if (type is not ("DEBIT" or "CREDIT")) throw new InvalidPaymentException("Payment type is invalid");
        if ((type == "DEBIT" && request.Installments != 1) || request.Installments is < 1 or > 12)
            throw new InvalidPaymentException("Installments are invalid");
        return pan;
    }

    private static bool ValidDecision(Guid id, string paymentType, string pan, IssuerPaymentResult decision)
    {
        if (decision.PaymentId != id || decision.Last4 != pan[^4..]
            || decision.CardType != paymentType) return false;
        return decision.Status switch
        {
            "CAPTURED" => !string.IsNullOrWhiteSpace(decision.AuthorizationCode)
                && decision.AuthorizationCode.Length <= 20
                && string.IsNullOrWhiteSpace(decision.DeclineCode),
            "DECLINED" => string.IsNullOrWhiteSpace(decision.AuthorizationCode)
                && !string.IsNullOrWhiteSpace(decision.DeclineCode)
                && decision.DeclineCode.Length <= 40,
            _ => false
        };
    }

    private static bool Luhn(string pan)
    {
        var sum = 0;
        var alternate = false;
        for (var i = pan.Length - 1; i >= 0; i--)
        {
            var digit = pan[i] - '0';
            if (alternate && (digit *= 2) > 9) digit -= 9;
            sum += digit;
            alternate = !alternate;
        }
        return sum % 10 == 0;
    }

}
