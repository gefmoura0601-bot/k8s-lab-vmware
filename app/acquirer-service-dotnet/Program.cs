using Npgsql;
using Prometheus;

var builder = WebApplication.CreateBuilder(args);
builder.Services.AddOpenApi();
builder.Services.AddSingleton<NpgsqlDataSource>(_ => NpgsqlDataSource.Create(
    builder.Configuration.GetConnectionString("Acquiring")
        ?? throw new InvalidOperationException("ConnectionStrings__Acquiring is required")));
builder.Services.AddHealthChecks()
    .AddCheck<PostgresHealthCheck>("postgres", tags: ["ready"]);
builder.Services.AddSingleton<IPaymentRepository, PostgresPaymentRepository>();
builder.Services.AddSingleton(new PaymentFingerprint(
    builder.Configuration["ACQUIRER_IDEMPOTENCY_SECRET"] ?? string.Empty));
builder.Services.AddScoped<PaymentCoordinator>();
builder.Services.AddHttpClient<IIssuerClient, IssuerClient>(client =>
{
    client.BaseAddress = new Uri(builder.Configuration["IssuerUrl"]
        ?? "http://account-service.banking.svc.cluster.local");
    client.Timeout = TimeSpan.FromSeconds(10);
});
builder.Services.AddHostedService<DatabaseInitializer>();

var app = builder.Build();
var payments = Metrics.CreateCounter("banking_card_acquiring_payments_total",
    "Acquiring payments grouped by decision", new CounterConfiguration { LabelNames = ["status"] });
app.UseHttpMetrics();
app.MapOpenApi();
app.MapHealthChecks("/health/live", new() { Predicate = _ => false });
app.MapHealthChecks("/health/ready", new() { Predicate = check => check.Tags.Contains("ready") });
app.MapMetrics();

app.MapPost("/internal/v1/payments", async (HttpRequest httpRequest, CreatePaymentRequest request,
    PaymentCoordinator coordinator, CancellationToken cancellationToken) =>
{
    if (!httpRequest.Headers.TryGetValue("Idempotency-Key", out var value) || !Guid.TryParse(value, out var id))
        return Results.BadRequest(new ApiError("invalid_idempotency_key", "Idempotency-Key must be a UUID"));
    try
    {
        var result = await coordinator.ExecuteAsync(id, request, cancellationToken);
        payments.WithLabels(result.Status.ToLowerInvariant()).Inc();
        return Results.Ok(result);
    }
    catch (InvalidPaymentException exception)
    {
        payments.WithLabels("rejected").Inc();
        return Results.BadRequest(new ApiError("invalid_payment", exception.Message));
    }
    catch (PaymentConflictException)
    {
        return Results.Conflict(new ApiError("idempotency_conflict", "Idempotency-Key was used with another payment"));
    }
    catch (IssuerUnavailableException)
    {
        payments.WithLabels("technical_failure").Inc();
        return Results.Json(new ApiError("issuer_unavailable", "Issuer is temporarily unavailable"), statusCode: 502);
    }
});

app.MapGet("/internal/v1/payments/{id:guid}", async (Guid id, IPaymentRepository repository,
    CancellationToken cancellationToken) =>
    await repository.GetAsync(id, cancellationToken) is { } payment ? Results.Ok(payment) : Results.NotFound());

app.Run();

public partial class Program;
