using System.Security.Cryptography;
using System.Globalization;
using System.Text;
using System.Text.Json;

public sealed class PaymentFingerprint
{
    private static readonly byte[] Domain = Encoding.UTF8.GetBytes("moura-acquirer-idempotency-v1\0");
    private readonly byte[] key;

    public PaymentFingerprint(string secret)
    {
        if (string.IsNullOrEmpty(secret) || Encoding.UTF8.GetByteCount(secret) < 32)
            throw new InvalidOperationException("ACQUIRER_IDEMPOTENCY_SECRET must contain at least 32 bytes");
        key = Encoding.UTF8.GetBytes(secret);
    }

    public string Compute(CreatePaymentRequest request, string normalizedPan)
    {
        var canonical = JsonSerializer.SerializeToUtf8Bytes(new
        {
            version = 1,
            merchantId = request.MerchantId.Trim(),
            merchantName = request.MerchantName.Trim(),
            orderId = request.OrderId,
            amount = request.Amount.ToString("0.00", CultureInfo.InvariantCulture),
            currency = request.Currency.Trim().ToUpperInvariant(),
            description = request.Description.Trim(),
            card = new
            {
                number = normalizedPan,
                holderName = request.Card.HolderName.Trim(),
                request.Card.ExpiryMonth,
                request.Card.ExpiryYear,
                cvv = request.Card.Cvv
            },
            paymentType = request.PaymentType.Trim().ToUpperInvariant(),
            request.Installments
        });
        var input = new byte[Domain.Length + canonical.Length];
        Buffer.BlockCopy(Domain, 0, input, 0, Domain.Length);
        Buffer.BlockCopy(canonical, 0, input, Domain.Length, canonical.Length);
        try
        {
            return Convert.ToHexStringLower(HMACSHA256.HashData(key, input));
        }
        finally
        {
            CryptographicOperations.ZeroMemory(input);
            CryptographicOperations.ZeroMemory(canonical);
        }
    }
}
