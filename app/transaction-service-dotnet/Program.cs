using System.ComponentModel.DataAnnotations;
using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using System.Text.Json.Serialization;
using Npgsql;
using Prometheus;
using Prometheus.DotNetRuntime;

var builder = WebApplication.CreateBuilder(args);
using var runtimeCollector = DotNetRuntimeStatsBuilder.Default().StartCollecting();
builder.Services.AddOpenApi();
builder.Services.ConfigureHttpJsonOptions(options =>
    options.SerializerOptions.Converters.Add(new JsonStringEnumConverter(JsonNamingPolicy.SnakeCaseUpper)));
builder.Services.AddHealthChecks();
builder.Services.AddSingleton<NpgsqlDataSource>(_ =>
{
    var connectionString = builder.Configuration.GetConnectionString("Transactions")
        ?? throw new InvalidOperationException("ConnectionStrings__Transactions is required");
    return NpgsqlDataSource.Create(connectionString);
});
builder.Services.AddSingleton<ITransactionRepository, PostgresTransactionRepository>();
builder.Services.AddScoped<TransferCoordinator>();
builder.Services.AddHttpClient<IAccountClient, AccountClient>(client =>
{
    client.BaseAddress = new Uri(builder.Configuration["AccountServiceUrl"]
        ?? "http://account-service.banking.svc.cluster.local");
    client.Timeout = TimeSpan.FromSeconds(10);
});
builder.Services.AddHostedService<DatabaseInitializer>();

var app = builder.Build();
app.UseHttpMetrics();
app.MapOpenApi();
app.MapHealthChecks("/health/live", new() { Predicate = _ => false });
app.MapHealthChecks("/health/ready");
app.MapMetrics();

app.MapPost("/api/v1/transactions", async (
    HttpRequest httpRequest,
    TransferRequest request,
    IAccountClient accountClient,
    TransferCoordinator coordinator,
    CancellationToken cancellationToken) =>
{
    if (!httpRequest.Headers.TryGetValue("Idempotency-Key", out var rawKey)
        || !Guid.TryParse(rawKey, out var idempotencyKey))
    {
        return Results.BadRequest(new ApiError("invalid_idempotency_key", "Idempotency-Key must be a UUID"));
    }
    var validation = Validate(request);
    if (validation is not null) return Results.BadRequest(validation);

    try
    {
        await accountClient.AuthorizeAsync(request.SourceAccountId, httpRequest.Headers.Cookie.ToString(), cancellationToken);
        var transaction = await coordinator.ExecuteAsync(idempotencyKey, request, cancellationToken);
        return transaction.Status == TransactionStatus.Completed
            ? Results.Ok(transaction)
            : Results.Accepted($"/api/v1/transactions/{transaction.Id}", transaction);
    }
    catch (AccountTransferException exception)
    {
        return Results.Json(
            new ApiError(exception.Code, exception.Message),
            statusCode: exception.StatusCode);
    }
})
.WithName("CreateTransaction")
.Produces<Transaction>(StatusCodes.Status200OK)
.Produces<Transaction>(StatusCodes.Status202Accepted)
.Produces<ApiError>(StatusCodes.Status400BadRequest)
.Produces<ApiError>(StatusCodes.Status422UnprocessableEntity);

app.MapGet("/api/v1/transactions/{id:guid}", async (
    Guid id,
    HttpRequest httpRequest,
    IAccountClient accountClient,
    ITransactionRepository repository,
    CancellationToken cancellationToken) =>
{
    var transaction = await repository.GetAsync(id, cancellationToken);
    if (transaction is null) return Results.NotFound();
    try
    {
        await accountClient.AuthorizeAsync(transaction.SourceAccountId, httpRequest.Headers.Cookie.ToString(), cancellationToken);
        return Results.Ok(transaction);
    }
    catch (AccountTransferException exception)
    {
        return Results.Json(new ApiError(exception.Code, exception.Message), statusCode: exception.StatusCode);
    }
})
.WithName("GetTransaction")
.Produces<Transaction>()
.Produces(StatusCodes.Status404NotFound);

app.Run();

static ApiError? Validate(TransferRequest request)
{
    if (request.SourceAccountId == request.DestinationAccountId)
        return new("same_account", "Source and destination accounts must differ");
    if (request.Amount <= 0 || decimal.Round(request.Amount, 2) != request.Amount)
        return new("invalid_amount", "Amount must be positive with at most two decimal places");
    if (string.IsNullOrWhiteSpace(request.Description) || request.Description.Length > 140)
        return new("invalid_description", "Description is required and limited to 140 characters");
    return null;
}

public partial class Program;
