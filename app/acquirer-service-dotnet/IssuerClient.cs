using System.Net.Http.Json;

public sealed class IssuerClient(HttpClient client) : IIssuerClient
{
    public async Task<IssuerPaymentResult> PayAsync(Guid id, CreatePaymentRequest request, string normalizedPan,
        CancellationToken cancellationToken)
    {
        try
        {
            var issuerRequest = new IssuerPaymentRequest(
                id, request.MerchantId.Trim(), request.MerchantName.Trim(), request.OrderId.ToString(),
                request.Amount, request.Currency.Trim().ToUpperInvariant(),
                request.Card with { Number = normalizedPan },
                request.PaymentType.Trim().ToUpperInvariant(), request.Installments);
            using var response = await client.PostAsJsonAsync(
                "/internal/v1/card-payments", issuerRequest, cancellationToken);
            if (response.StatusCode == System.Net.HttpStatusCode.Conflict) throw new PaymentConflictException();
            if (!response.IsSuccessStatusCode)
                throw new IssuerUnavailableException($"Issuer returned HTTP {(int)response.StatusCode}");
            return await response.Content.ReadFromJsonAsync<IssuerPaymentResult>(cancellationToken)
                ?? throw new IssuerUnavailableException("Issuer returned an empty response");
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            throw;
        }
        catch (PaymentConflictException)
        {
            throw;
        }
        catch (IssuerUnavailableException)
        {
            throw;
        }
        catch (Exception exception) when (exception is HttpRequestException or OperationCanceledException
            or System.Text.Json.JsonException)
        {
            throw new IssuerUnavailableException("Issuer communication failed");
        }
    }
}
