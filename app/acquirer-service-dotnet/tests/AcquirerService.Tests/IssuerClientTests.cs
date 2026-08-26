using System.Net;
using System.Text;
using System.Text.Json;
using Xunit;

public sealed class IssuerClientTests
{
    private static readonly Guid PaymentId = Guid.Parse("11111111-1111-1111-1111-111111111111");
    private static readonly Guid OrderId = Guid.Parse("22222222-2222-2222-2222-222222222222");
    private static readonly CreatePaymentRequest Request = new(
        " moura-store ", " Moura Store ", OrderId, 49.90m, "brl", " Produto de laboratório ",
        new CardInput("9999 9900 0000 0006", "CLIENTE LAB", 12, 2099, "123"), " debit ", 1);

    [Fact]
    public async Task SendsIssuerContractWithNormalizedRoutingFields()
    {
        var handler = new StubHandler(async (request, cancellationToken) =>
        {
            Assert.Equal(HttpMethod.Post, request.Method);
            Assert.Equal("/internal/v1/card-payments", request.RequestUri!.AbsolutePath);
            var body = await request.Content!.ReadAsStringAsync(cancellationToken);
            using var document = JsonDocument.Parse(body);
            var root = document.RootElement;
            Assert.Equal(PaymentId, root.GetProperty("paymentId").GetGuid());
            Assert.Equal("moura-store", root.GetProperty("merchantId").GetString());
            Assert.Equal("Moura Store", root.GetProperty("merchantName").GetString());
            Assert.Equal(OrderId.ToString(), root.GetProperty("orderReference").GetString());
            Assert.Equal("BRL", root.GetProperty("currency").GetString());
            Assert.Equal("DEBIT", root.GetProperty("paymentType").GetString());
            Assert.Equal("9999990000000006",
                root.GetProperty("card").GetProperty("number").GetString());

            return JsonResponse(HttpStatusCode.OK,
                $$"""{"paymentId":"{{PaymentId}}","status":"CAPTURED","authorizationCode":"123456","declineCode":null,"cardType":"DEBIT","last4":"0006"}""");
        });
        var issuer = CreateClient(handler);

        var result = await issuer.PayAsync(PaymentId, Request, "9999990000000006", CancellationToken.None);

        Assert.Equal("CAPTURED", result.Status);
        Assert.Equal("DEBIT", result.CardType);
    }

    [Fact]
    public async Task MapsIssuerConflictToIdempotencyConflict()
    {
        var issuer = CreateClient(new StubHandler((_, _) =>
            Task.FromResult(new HttpResponseMessage(HttpStatusCode.Conflict))));

        await Assert.ThrowsAsync<PaymentConflictException>(() =>
            issuer.PayAsync(PaymentId, Request, "9999990000000006", CancellationToken.None));
    }

    [Fact]
    public async Task MapsInvalidIssuerJsonToUnavailable()
    {
        var issuer = CreateClient(new StubHandler((_, _) =>
            Task.FromResult(JsonResponse(HttpStatusCode.OK, "{invalid"))));

        await Assert.ThrowsAsync<IssuerUnavailableException>(() =>
            issuer.PayAsync(PaymentId, Request, "9999990000000006", CancellationToken.None));
    }

    [Fact]
    public async Task MapsIssuerServerFailureToUnavailable()
    {
        var issuer = CreateClient(new StubHandler((_, _) =>
            Task.FromResult(new HttpResponseMessage(HttpStatusCode.ServiceUnavailable))));

        await Assert.ThrowsAsync<IssuerUnavailableException>(() =>
            issuer.PayAsync(PaymentId, Request, "9999990000000006", CancellationToken.None));
    }

    private static IssuerClient CreateClient(HttpMessageHandler handler) =>
        new(new HttpClient(handler) { BaseAddress = new Uri("http://issuer.test") });

    private static HttpResponseMessage JsonResponse(HttpStatusCode status, string json) =>
        new(status)
        {
            Content = new StringContent(json, Encoding.UTF8, "application/json")
        };

    private sealed class StubHandler(
        Func<HttpRequestMessage, CancellationToken, Task<HttpResponseMessage>> response) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(
            HttpRequestMessage request,
            CancellationToken cancellationToken) => response(request, cancellationToken);
    }
}
