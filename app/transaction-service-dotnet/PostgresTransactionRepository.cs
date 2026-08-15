using Npgsql;

public sealed class PostgresTransactionRepository(NpgsqlDataSource dataSource) : ITransactionRepository
{
    public async Task<Transaction?> GetAsync(Guid id, CancellationToken cancellationToken)
    {
        await using var command = dataSource.CreateCommand(
            """
            SELECT id, source_account_id, destination_account_id, amount, description,
                   status, created_at, completed_at, failure_code
            FROM transaction_service.transactions WHERE id = $1
            """);
        command.Parameters.AddWithValue(id);
        await using var reader = await command.ExecuteReaderAsync(cancellationToken);
        return await reader.ReadAsync(cancellationToken) ? Read(reader) : null;
    }

    public async Task<IReadOnlyList<Transaction>> ListBySourceAsync(Guid sourceAccountId, CancellationToken cancellationToken)
    {
        await using var command = dataSource.CreateCommand(
            """
            SELECT id, source_account_id, destination_account_id, amount, description,
                   status, created_at, completed_at, failure_code
            FROM transaction_service.transactions
            WHERE source_account_id = $1 OR destination_account_id = $1
            ORDER BY created_at DESC
            LIMIT 100
            """);
        command.Parameters.AddWithValue(sourceAccountId);
        await using var reader = await command.ExecuteReaderAsync(cancellationToken);
        var transactions = new List<Transaction>();
        while (await reader.ReadAsync(cancellationToken)) transactions.Add(Read(reader));
        return transactions;
    }
    public async Task<Transaction> CreateOrGetAsync(
        Guid id,
        TransferRequest request,
        CancellationToken cancellationToken)
    {
        await using var command = dataSource.CreateCommand(
            """
            INSERT INTO transaction_service.transactions
                (id, source_account_id, destination_account_id, amount, description, status)
            VALUES ($1, $2, $3, $4, $5, 'Pending')
            ON CONFLICT (id) DO NOTHING
            """);
        command.Parameters.AddWithValue(id);
        command.Parameters.AddWithValue(request.SourceAccountId);
        command.Parameters.AddWithValue(request.DestinationAccountId);
        command.Parameters.AddWithValue(request.Amount);
        command.Parameters.AddWithValue(request.Description.Trim());
        await command.ExecuteNonQueryAsync(cancellationToken);
        return await GetAsync(id, cancellationToken)
            ?? throw new InvalidOperationException("Transaction disappeared after insert");
    }

    public Task<Transaction> CompleteAsync(Guid id, CancellationToken cancellationToken) =>
        UpdateAsync(id, TransactionStatus.Completed, null, cancellationToken);

    public Task<Transaction> FailAsync(Guid id, string failureCode, CancellationToken cancellationToken) =>
        UpdateAsync(id, TransactionStatus.Failed, failureCode, cancellationToken);

    private async Task<Transaction> UpdateAsync(
        Guid id,
        TransactionStatus status,
        string? failureCode,
        CancellationToken cancellationToken)
    {
        await using var command = dataSource.CreateCommand(
            """
            UPDATE transaction_service.transactions
            SET status = $2,
                completed_at = CASE WHEN $2 = 'Completed' THEN now() ELSE completed_at END,
                failure_code = $3
            WHERE id = $1
            """);
        command.Parameters.AddWithValue(id);
        command.Parameters.AddWithValue(status.ToString());
        command.Parameters.AddWithValue(failureCode is null ? DBNull.Value : failureCode);
        await command.ExecuteNonQueryAsync(cancellationToken);
        return await GetAsync(id, cancellationToken)
            ?? throw new InvalidOperationException("Transaction disappeared after update");
    }

    private static Transaction Read(NpgsqlDataReader reader) => new(
        reader.GetGuid(0),
        reader.GetGuid(1),
        reader.GetGuid(2),
        reader.GetDecimal(3),
        reader.GetString(4),
        Enum.Parse<TransactionStatus>(reader.GetString(5)),
        reader.GetFieldValue<DateTimeOffset>(6),
        reader.IsDBNull(7) ? null : reader.GetFieldValue<DateTimeOffset>(7),
        reader.IsDBNull(8) ? null : reader.GetString(8));
}
