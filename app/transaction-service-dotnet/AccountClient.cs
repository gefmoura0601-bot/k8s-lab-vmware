using System.Net;
using System.Net.Http.Json;

public sealed class AccountClient(HttpClient client) : IAccountClient
{
    public async Task ApplyTransferAsync(
        Guid transactionId,
        TransferRequest request,
        CancellationToken cancellationToken)
    {
        var response = await client.PostAsJsonAsync(
            "/internal/v1/transfers",
            new
            {
                transactionId,
                request.SourceAccountId,
                request.DestinationAccountId,
                request.Amount
            },
            cancellationToken);

        if (response.IsSuccessStatusCode) return;

        var error = await response.Content.ReadFromJsonAsync<ApiError>(cancellationToken: cancellationToken);
        throw new AccountTransferException(
            (int)response.StatusCode,
            error?.Code ?? "account_service_error",
            error?.Message ?? "Account service rejected the transfer");
    }
}

public sealed class AccountTransferException(int statusCode, string code, string message) : Exception(message)
{
    public int StatusCode { get; } = statusCode;
    public string Code { get; } = code;
}
