using Microsoft.Extensions.Logging.Abstractions;
using Xunit;

public sealed class DatabaseInitializationRetryTests
{
    [Fact]
    public async Task RetriesTransientFailureUntilOperationSucceeds()
    {
        var attempts = 0;

        await DatabaseInitializationRetry.ExecuteAsync(
            _ =>
            {
                attempts++;
                return attempts < 3
                    ? Task.FromException(new TimeoutException("database unavailable"))
                    : Task.CompletedTask;
            },
            maxAttempts: 4,
            initialDelay: TimeSpan.Zero,
            maxDelay: TimeSpan.Zero,
            NullLogger.Instance,
            CancellationToken.None);

        Assert.Equal(3, attempts);
    }

    [Fact]
    public async Task DoesNotRetryNonTransientFailure()
    {
        var attempts = 0;

        await Assert.ThrowsAsync<InvalidOperationException>(() =>
            DatabaseInitializationRetry.ExecuteAsync(
                _ =>
                {
                    attempts++;
                    return Task.FromException(new InvalidOperationException("invalid schema"));
                },
                maxAttempts: 4,
                initialDelay: TimeSpan.Zero,
                maxDelay: TimeSpan.Zero,
                NullLogger.Instance,
                CancellationToken.None));

        Assert.Equal(1, attempts);
    }

    [Fact]
    public async Task StopsAfterConfiguredTransientAttempts()
    {
        var attempts = 0;

        await Assert.ThrowsAsync<TimeoutException>(() =>
            DatabaseInitializationRetry.ExecuteAsync(
                _ =>
                {
                    attempts++;
                    return Task.FromException(new TimeoutException("database unavailable"));
                },
                maxAttempts: 3,
                initialDelay: TimeSpan.Zero,
                maxDelay: TimeSpan.Zero,
                NullLogger.Instance,
                CancellationToken.None));

        Assert.Equal(3, attempts);
    }
}
