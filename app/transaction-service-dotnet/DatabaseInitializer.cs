using Npgsql;

public sealed class DatabaseInitializer(
    NpgsqlDataSource dataSource,
    ILogger<DatabaseInitializer> logger,
    IConfiguration configuration) : IHostedService
{
    public async Task StartAsync(CancellationToken cancellationToken)
    {
        var maxAttempts = Math.Clamp(
            configuration.GetValue<int?>("DatabaseInitialization:MaxAttempts") ?? 15,
            1,
            60);
        var initialDelay = TimeSpan.FromMilliseconds(Math.Clamp(
            configuration.GetValue<int?>("DatabaseInitialization:InitialDelayMilliseconds") ?? 1000,
            0,
            30_000));
        var maxDelay = TimeSpan.FromMilliseconds(Math.Clamp(
            configuration.GetValue<int?>("DatabaseInitialization:MaxDelayMilliseconds") ?? 5000,
            (int)initialDelay.TotalMilliseconds,
            30_000));

        await DatabaseInitializationRetry.ExecuteAsync(
            InitializeSchemaAsync,
            maxAttempts,
            initialDelay,
            maxDelay,
            logger,
            cancellationToken);
        logger.LogInformation("Transaction database schema is ready");
    }

    private async Task InitializeSchemaAsync(CancellationToken cancellationToken)
    {
        await using var command = dataSource.CreateCommand(
            """
            CREATE SCHEMA IF NOT EXISTS transaction_service;
            CREATE TABLE IF NOT EXISTS transaction_service.transactions (
                id UUID PRIMARY KEY,
                source_account_id UUID NOT NULL,
                destination_account_id UUID NOT NULL,
                amount NUMERIC(19,2) NOT NULL CHECK (amount > 0),
                description VARCHAR(140) NOT NULL,
                status VARCHAR(20) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                completed_at TIMESTAMPTZ NULL,
                failure_code VARCHAR(80) NULL
            );
            CREATE INDEX IF NOT EXISTS idx_transactions_source
                ON transaction_service.transactions(source_account_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_transactions_destination
                ON transaction_service.transactions(destination_account_id, created_at DESC);
            """);
        await command.ExecuteNonQueryAsync(cancellationToken);
    }

    public Task StopAsync(CancellationToken cancellationToken) => Task.CompletedTask;
}
