using Xunit;

public sealed class PaymentCoordinatorTests
{
    private const string TestSecret = "test-only-acquirer-idempotency-secret-0001";
    private static readonly Guid Id = Guid.Parse("11111111-1111-1111-1111-111111111111");
    private static readonly CreatePaymentRequest Request = new(
        "moura-store", "Moura Store", Guid.Parse("22222222-2222-2222-2222-222222222222"),
        49.90m, "BRL", "Produto de laboratório",
        new CardInput("9999990000000006", "CLIENTE LAB", 12, 2099, "123"), "DEBIT", 1);

    [Fact]
    public async Task CapturesPaymentAndPersistsSanitizedDecision()
    {
        var repository = new MemoryRepository();
        var coordinator = Coordinator(repository, new FakeIssuer());
        var payment = await coordinator.ExecuteAsync(Id, Request, CancellationToken.None);
        Assert.Equal("CAPTURED", payment.Status);
        Assert.Equal("0006", payment.Last4);
        Assert.Equal(1, repository.Decisions);
    }

    [Fact]
    public async Task ReturnsTerminalPaymentWithoutCallingIssuerAgain()
    {
        var repository = new MemoryRepository();
        var issuer = new FakeIssuer();
        var coordinator = Coordinator(repository, issuer);
        await coordinator.ExecuteAsync(Id, Request, CancellationToken.None);
        await coordinator.ExecuteAsync(Id, Request, CancellationToken.None);
        Assert.Equal(1, issuer.Calls);
    }

    [Fact]
    public void RejectsInvalidPan() => Assert.Throws<InvalidPaymentException>(() =>
        PaymentCoordinator.Validate(Request with { Card = Request.Card with { Number = "9999990000000007" } }));

    [Fact]
    public void RejectsNonLaboratoryPan() => Assert.Throws<InvalidPaymentException>(() =>
        PaymentCoordinator.Validate(Request with { Card = Request.Card with { Number = "4111111111111111" } }));

    [Fact]
    public void RejectsPanWithIgnoredGarbage() => Assert.Throws<InvalidPaymentException>(() =>
        PaymentCoordinator.Validate(Request with { Card = Request.Card with { Number = "9999990000000006abc" } }));

    [Fact]
    public void AcceptsPanWithSpacesAndHyphens()
    {
        var pan = PaymentCoordinator.Validate(Request with
        {
            Card = Request.Card with { Number = "9999-9900 0000-0006" }
        });
        Assert.Equal("9999990000000006", pan);
    }

    [Fact]
    public void RejectsMissingCardWithoutThrowingNullReference() => Assert.Throws<InvalidPaymentException>(() =>
        PaymentCoordinator.Validate(Request with { Card = null! }));

    [Fact]
    public void FingerprintIsKeyedAndIncludesSecurityCode()
    {
        var fingerprint = new PaymentFingerprint(TestSecret);
        var first = fingerprint.Compute(Request, "9999990000000006");
        var second = fingerprint.Compute(Request with { Card = Request.Card with { Cvv = "124" } },
            "9999990000000006");
        Assert.NotEqual(first, second);
        Assert.Equal(64, first.Length);
    }

    [Fact]
    public void FingerprintCanonicalizesEquivalentMoneyScale()
    {
        var fingerprint = new PaymentFingerprint(TestSecret);
        Assert.Equal(
            fingerprint.Compute(Request with { Amount = 49.9m }, "9999990000000006"),
            fingerprint.Compute(Request with { Amount = 49.90m }, "9999990000000006"));
    }

    [Fact]
    public async Task RejectsIssuerDecisionWithMismatchedCardType()
    {
        var coordinator = Coordinator(new MemoryRepository(),
            new FakeIssuer(new IssuerPaymentResult(Id, "CAPTURED", "ABC123", null, "CREDIT", "0006")));
        await Assert.ThrowsAsync<IssuerUnavailableException>(() =>
            coordinator.ExecuteAsync(Id, Request, CancellationToken.None));
    }

    [Fact]
    public async Task RejectsIssuerDecisionThatCannotFitPersistenceSchema()
    {
        var coordinator = Coordinator(new MemoryRepository(),
            new FakeIssuer(new IssuerPaymentResult(Id, "DECLINED", null, new string('X', 41), "DEBIT", "0006")));
        await Assert.ThrowsAsync<IssuerUnavailableException>(() =>
            coordinator.ExecuteAsync(Id, Request, CancellationToken.None));
    }

    private static PaymentCoordinator Coordinator(IPaymentRepository repository, IIssuerClient issuer) =>
        new(repository, issuer, new PaymentFingerprint(TestSecret));

    private sealed class FakeIssuer(IssuerPaymentResult? result = null) : IIssuerClient
    {
        public int Calls { get; private set; }
        public Task<IssuerPaymentResult> PayAsync(Guid id, CreatePaymentRequest request, string normalizedPan,
            CancellationToken cancellationToken)
        {
            Calls++;
            return Task.FromResult(result
                ?? new IssuerPaymentResult(id, "CAPTURED", "ABC123", null, "DEBIT", "0006"));
        }
    }

    private sealed class MemoryRepository : IPaymentRepository
    {
        private Payment? payment;
        private string hash = "";
        public int Decisions { get; private set; }
        public Task<(Payment Payment, string RequestHash)> CreateOrGetAsync(Guid id, CreatePaymentRequest request,
            string normalizedPan, string requestHash, CancellationToken cancellationToken)
        {
            payment ??= new Payment(id, request.MerchantId, request.OrderId, request.Amount, request.Currency,
                request.Description, normalizedPan[^4..], request.PaymentType, request.PaymentType,
                request.Installments, "PENDING",
                null, null, DateTimeOffset.UtcNow, null);
            if (string.IsNullOrEmpty(hash)) hash = requestHash;
            return Task.FromResult((payment, hash));
        }
        public Task<Payment?> GetAsync(Guid id, CancellationToken cancellationToken) => Task.FromResult(payment);
        public Task<Payment> DecideAsync(Guid id, IssuerPaymentResult result, CancellationToken cancellationToken)
        {
            Decisions++;
            payment = payment! with
            {
                Status = result.Status,
                AuthorizationCode = result.AuthorizationCode,
                DeclineCode = result.DeclineCode,
                CompletedAt = DateTimeOffset.UtcNow
            };
            return Task.FromResult(payment);
        }
    }
}
