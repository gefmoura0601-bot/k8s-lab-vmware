using Npgsql;
using NpgsqlTypes;

public sealed class PostgresPaymentRepository(NpgsqlDataSource dataSource) : IPaymentRepository
{
    public async Task<(Payment Payment, string RequestHash)> CreateOrGetAsync(
        Guid id, CreatePaymentRequest request, string normalizedPan, string requestHash,
        CancellationToken cancellationToken)
    {
        await using var command = dataSource.CreateCommand(
            """
            INSERT INTO acquiring_service.payments
                (payment_id, merchant_id, order_id, amount, currency, description, card_last4,
                 payment_type, installments, request_hash, status)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,'PENDING')
            ON CONFLICT (payment_id) DO NOTHING
            """);
        command.Parameters.AddWithValue(id);
        command.Parameters.AddWithValue(request.MerchantId.Trim());
        command.Parameters.AddWithValue(request.OrderId);
        command.Parameters.AddWithValue(request.Amount);
        command.Parameters.AddWithValue(request.Currency.ToUpperInvariant());
        command.Parameters.AddWithValue(request.Description.Trim());
        command.Parameters.AddWithValue(normalizedPan[^4..]);
        command.Parameters.AddWithValue(request.PaymentType.Trim().ToUpperInvariant());
        command.Parameters.AddWithValue(request.Installments);
        command.Parameters.AddWithValue(requestHash);
        await command.ExecuteNonQueryAsync(cancellationToken);
        return await GetWithHashAsync(id, cancellationToken)
            ?? throw new InvalidOperationException("Payment disappeared after insert");
    }

    public async Task<Payment?> GetAsync(Guid id, CancellationToken cancellationToken) =>
        (await GetWithHashAsync(id, cancellationToken))?.Payment;

    public async Task<Payment> DecideAsync(Guid id, IssuerPaymentResult result, CancellationToken cancellationToken)
    {
        await using var command = dataSource.CreateCommand(
            """
            UPDATE acquiring_service.payments
            SET status=$2, authorization_code=$3, decline_code=$4, card_type=$5, completed_at=now()
            WHERE payment_id=$1 AND status='PENDING'
            """);
        command.Parameters.AddWithValue(id);
        command.Parameters.AddWithValue(result.Status);
        command.Parameters.Add(new NpgsqlParameter
        {
            NpgsqlDbType = NpgsqlDbType.Varchar,
            Value = (object?)result.AuthorizationCode ?? DBNull.Value
        });
        command.Parameters.Add(new NpgsqlParameter
        {
            NpgsqlDbType = NpgsqlDbType.Varchar,
            Value = (object?)result.DeclineCode ?? DBNull.Value
        });
        command.Parameters.Add(new NpgsqlParameter
        {
            NpgsqlDbType = NpgsqlDbType.Varchar,
            Value = (object?)result.CardType ?? DBNull.Value
        });
        await command.ExecuteNonQueryAsync(cancellationToken);
        return await GetAsync(id, cancellationToken)
            ?? throw new InvalidOperationException("Payment disappeared after decision");
    }

    private async Task<(Payment Payment, string RequestHash)?> GetWithHashAsync(Guid id, CancellationToken cancellationToken)
    {
        await using var command = dataSource.CreateCommand(
            """
            SELECT payment_id,merchant_id,order_id,amount,currency,description,card_last4,
                   payment_type,card_type,installments,status,authorization_code,decline_code,created_at,
                   completed_at,request_hash
            FROM acquiring_service.payments WHERE payment_id=$1
            """);
        command.Parameters.AddWithValue(id);
        await using var reader = await command.ExecuteReaderAsync(cancellationToken);
        if (!await reader.ReadAsync(cancellationToken)) return null;
        var payment = new Payment(
            reader.GetGuid(0), reader.GetString(1), reader.GetGuid(2), reader.GetDecimal(3),
            reader.GetString(4), reader.GetString(5), reader.GetString(6), reader.GetString(7),
            reader.IsDBNull(8) ? reader.GetString(7) : reader.GetString(8), reader.GetInt32(9),
            reader.GetString(10), reader.IsDBNull(11) ? null : reader.GetString(11),
            reader.IsDBNull(12) ? null : reader.GetString(12), reader.GetFieldValue<DateTimeOffset>(13),
            reader.IsDBNull(14) ? null : reader.GetFieldValue<DateTimeOffset>(14));
        return (payment, reader.GetString(15));
    }
}
