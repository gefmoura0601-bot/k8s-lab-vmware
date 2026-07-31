public sealed class TransferCoordinator(
    ITransactionRepository repository,
    IAccountClient accountClient,
    ILogger<TransferCoordinator> logger)
{
    public async Task<Transaction> ExecuteAsync(
        Guid idempotencyKey,
        TransferRequest request,
        CancellationToken cancellationToken)
    {
        var transaction = await repository.CreateOrGetAsync(idempotencyKey, request, cancellationToken);
        EnsureSamePayload(transaction, request);
        if (transaction.Status == TransactionStatus.Completed) return transaction;

        try
        {
            await accountClient.ApplyTransferAsync(transaction.Id, request, cancellationToken);
            return await repository.CompleteAsync(transaction.Id, cancellationToken);
        }
        catch (AccountTransferException exception) when (exception.StatusCode < 500)
        {
            logger.LogWarning(
                "Transaction {TransactionId} rejected by account service: {FailureCode}",
                transaction.Id,
                exception.Code);
            await repository.FailAsync(transaction.Id, exception.Code, cancellationToken);
            throw;
        }
    }

    private static void EnsureSamePayload(Transaction current, TransferRequest request)
    {
        if (current.SourceAccountId != request.SourceAccountId
            || current.DestinationAccountId != request.DestinationAccountId
            || current.Amount != request.Amount
            || current.Description != request.Description.Trim())
        {
            throw new AccountTransferException(
                StatusCodes.Status409Conflict,
                "idempotency_conflict",
                "Idempotency-Key was already used with a different payload");
        }
    }
}
