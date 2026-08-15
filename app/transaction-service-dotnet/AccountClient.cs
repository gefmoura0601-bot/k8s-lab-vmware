using System.Net.Http.Json;
public sealed class AccountClient(HttpClient client) : IAccountClient
{
    public async Task AuthorizeAsync(Guid sourceAccountId,string cookie,CancellationToken ct)
    {
        using var request=new HttpRequestMessage(HttpMethod.Post,"/internal/v1/auth/authorize"){Content=JsonContent.Create(new{sourceAccountId})};
        request.Headers.TryAddWithoutValidation("Cookie",cookie);
        var response=await client.SendAsync(request,ct);
        if(response.IsSuccessStatusCode)return;
        var error=await response.Content.ReadFromJsonAsync<ApiError>(cancellationToken:ct);
        throw new AccountTransferException((int)response.StatusCode,error?.Code??"unauthorized",error?.Message??"Authentication is required");
    }
    public async Task ApplyTransferAsync(Guid transactionId,TransferRequest request,CancellationToken ct)
    {
        var response=await client.PostAsJsonAsync("/internal/v1/transfers",new{transactionId,request.SourceAccountId,request.DestinationAccountId,request.Amount},ct);
        if(response.IsSuccessStatusCode)return;
        var error=await response.Content.ReadFromJsonAsync<ApiError>(cancellationToken:ct);
        throw new AccountTransferException((int)response.StatusCode,error?.Code??"account_service_error",error?.Message??"Account service rejected the transfer");
    }
}
public sealed class AccountTransferException(int statusCode,string code,string message):Exception(message){public int StatusCode{get;}=statusCode;public string Code{get;}=code;}
