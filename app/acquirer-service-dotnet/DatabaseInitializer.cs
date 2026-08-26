using Npgsql;

public sealed class DatabaseInitializer(NpgsqlDataSource dataSource, ILogger<DatabaseInitializer> logger) : IHostedService
{
    public async Task StartAsync(CancellationToken cancellationToken)
    {
        await using var command = dataSource.CreateCommand(
            """
            CREATE SCHEMA IF NOT EXISTS acquiring_service;
            CREATE TABLE IF NOT EXISTS acquiring_service.payments (
                payment_id UUID PRIMARY KEY,
                merchant_id VARCHAR(40) NOT NULL,
                order_id UUID NOT NULL,
                amount NUMERIC(19,2) NOT NULL CHECK (amount > 0),
                currency CHAR(3) NOT NULL,
                description VARCHAR(140) NOT NULL,
                card_last4 CHAR(4) NOT NULL,
                payment_type VARCHAR(10) NOT NULL,
                card_type VARCHAR(10),
                installments INTEGER NOT NULL,
                request_hash CHAR(64) NOT NULL,
                status VARCHAR(20) NOT NULL,
                authorization_code VARCHAR(20),
                decline_code VARCHAR(40),
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                completed_at TIMESTAMPTZ
            );
            ALTER TABLE acquiring_service.payments
                ADD COLUMN IF NOT EXISTS card_type VARCHAR(10);
            CREATE INDEX IF NOT EXISTS idx_acquiring_payments_merchant
                ON acquiring_service.payments(merchant_id, created_at DESC);
            """);
        await command.ExecuteNonQueryAsync(cancellationToken);
        logger.LogInformation("Acquiring database schema is ready");
    }

    public Task StopAsync(CancellationToken cancellationToken) => Task.CompletedTask;
}
