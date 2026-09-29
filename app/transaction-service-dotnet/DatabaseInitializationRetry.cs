using System.IO;
using System.Net.Sockets;
using Npgsql;

public static class DatabaseInitializationRetry
{
    public static async Task ExecuteAsync(
        Func<CancellationToken, Task> operation,
        int maxAttempts,
        TimeSpan initialDelay,
        TimeSpan maxDelay,
        ILogger logger,
        CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(operation);
        ArgumentNullException.ThrowIfNull(logger);
        ArgumentOutOfRangeException.ThrowIfLessThan(maxAttempts, 1);
        if (initialDelay < TimeSpan.Zero || maxDelay < initialDelay)
            throw new ArgumentOutOfRangeException(nameof(initialDelay));

        var delay = initialDelay;
        for (var attempt = 1; ; attempt++)
        {
            try
            {
                await operation(cancellationToken);
                return;
            }
            catch (Exception exception) when (attempt < maxAttempts && IsTransient(exception))
            {
                logger.LogWarning(
                    "Database initialization attempt {Attempt}/{MaxAttempts} failed with {ExceptionType}; retrying in {DelayMilliseconds} ms",
                    attempt,
                    maxAttempts,
                    exception.GetType().Name,
                    delay.TotalMilliseconds);
                await Task.Delay(delay, cancellationToken);
                delay = TimeSpan.FromMilliseconds(Math.Min(
                    maxDelay.TotalMilliseconds,
                    Math.Max(initialDelay.TotalMilliseconds, delay.TotalMilliseconds * 2)));
            }
        }
    }

    private static bool IsTransient(Exception exception)
    {
        if (exception is OperationCanceledException) return false;
        return exception is NpgsqlException or IOException or SocketException or TimeoutException
            || exception.InnerException is not null && IsTransient(exception.InnerException);
    }
}
