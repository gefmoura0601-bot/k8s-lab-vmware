using Microsoft.Extensions.Logging.Abstractions;
using Xunit;

public sealed class TransferCoordinatorTests
{
    [Fact]
    public async Task DuplicateCompletedTransactionDoesNotApplyTransferAgain()
    {
        var id = Guid.NewGuid();
        var request = new TransferRequest(Guid.NewGuid(), Guid.NewGuid(), 25.50m, "Invoice");
        var existing = new Transaction(
            id, request.SourceAccountId, request.DestinationAccountId, request.Amount,
            request.Description, TransactionStatus.Completed, DateTimeOffset.UtcNow,
            DateTimeOffset.UtcNow, null);
        var repository = new FakeRepository(existing);
        var accountClient = new FakeAccountClient();
        var coordinator = new TransferCoordinator(
            repository, accountClient, NullLogger<TransferCoordinator>.Instance);

        var result = await coordinator.ExecuteAsync(id, request, CancellationToken.None);

        Assert.Equal(TransactionStatus.Completed, result.Status);
        Assert.Equal(0, accountClient.CallCount);
    }

    [Fact]
    public async Task NewTransactionIsAppliedAndCompleted()
    {
        var id = Guid.NewGuid();
        var request = new TransferRequest(Guid.NewGuid(), Guid.NewGuid(), 10m, "Transfer");
        var repository = new FakeRepository(null);
        var accountClient = new FakeAccountClient();
        var coordinator = new TransferCoordinator(
            repository, accountClient, NullLogger<TransferCoordinator>.Instance);

        var result = await coordinator.ExecuteAsync(id, request, CancellationToken.None);

        Assert.Equal(TransactionStatus.Completed, result.Status);
        Assert.Equal(1, accountClient.CallCount);
    }

    private sealed class FakeAccountClient : IAccountClient
    {
        public Task AuthorizeAsync(Guid sourceAccountId, string sessionCookie, CancellationToken cancellationToken)
            => Task.CompletedTask;
        public Task ConfirmAsync(Guid sourceAccountId, string password, string sessionCookie, CancellationToken cancellationToken)
            => Task.CompletedTask;
        public Task<PixDestination> ResolvePixKeyAsync(Guid pixKey, CancellationToken cancellationToken)
            => Task.FromResult(new PixDestination(Guid.NewGuid(), "00000000", "Test"));

        public int CallCount { get; private set; }
        public Task ApplyTransferAsync(Guid transactionId, TransferRequest request, CancellationToken cancellationToken)
        {
            CallCount++;
            return Task.CompletedTask;
        }
    }

    private sealed class FakeRepository(Transaction? existing) : ITransactionRepository
    {
        private Transaction? value = existing;
        public Task<Transaction?> GetAsync(Guid id, CancellationToken cancellationToken) => Task.FromResult(value);
        public Task<IReadOnlyList<Transaction>> ListBySourceAsync(Guid sourceAccountId, CancellationToken cancellationToken)
            => Task.FromResult<IReadOnlyList<Transaction>>(value is null ? [] : [value]);
        public Task<Transaction> CreateOrGetAsync(Guid id, TransferRequest request, CancellationToken cancellationToken)
        {
            value ??= new(
                id, request.SourceAccountId, request.DestinationAccountId, request.Amount,
                request.Description, TransactionStatus.Pending, DateTimeOffset.UtcNow, null, null);
            return Task.FromResult(value);
        }
        public Task<Transaction> CompleteAsync(Guid id, CancellationToken cancellationToken)
        {
            value = value! with { Status = TransactionStatus.Completed, CompletedAt = DateTimeOffset.UtcNow };
            return Task.FromResult(value);
        }
        public Task<Transaction> FailAsync(Guid id, string failureCode, CancellationToken cancellationToken)
        {
            value = value! with { Status = TransactionStatus.Failed, FailureCode = failureCode };
            return Task.FromResult(value);
        }
    }
}
