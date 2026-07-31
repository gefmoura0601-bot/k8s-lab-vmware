# Runtime observability

The banking workloads expose continuous, low-overhead runtime metrics to
Prometheus and keep expensive diagnostic captures bounded and on demand.

## Grafana

The dashboard sidecar provisions these dashboards from Git:

- `Banking / JVM Runtime`
- `Banking / .NET Runtime`

Both dashboards can filter individual pods. The JVM dashboard includes heap,
non-heap, memory gap, buffers, threads, JIT, GC and Native Memory Tracking
(NMT). The .NET dashboard includes managed heap generations, available-memory
and native-memory gaps, allocation, GC, JIT, thread pool, contention,
exceptions and process resources.

## Java diagnostics

`JAVA_TOOL_OPTIONS` enables:

- continuous JFR with the low-overhead `default` profile, six-hour retention
  and a 256 MiB maximum;
- NMT summary mode, exported to Prometheus once per minute;
- frame pointers and non-safepoint debug information for profilers;
- an automatic heap dump on `OutOfMemoryError`.

Run a bounded high-detail JFR profile and collect an NMT snapshot:

```bash
JFR_DURATION=60s scripts/diagnostics/collect-java-runtime.sh
```

The command prints the `kubectl cp` command to run when JFR finishes. Diagnostic
files live on an ephemeral volume and disappear when the pod is replaced.

## .NET diagnostics

The runtime metrics collector listens to runtime events at its default,
low-overhead capture level. IPC and profiler diagnostics remain enabled.
`dotnet-monitor` runs as a non-root sidecar, binds only to pod-localhost, has no
Service or Ingress and shares only the diagnostic socket.

Collect a bounded CPU/EventPipe trace and a GC dump:

```bash
TRACE_DURATION_SECONDS=60 scripts/diagnostics/collect-dotnet-runtime.sh
```

The local artifacts can be opened with JDK Mission Control (`.jfr`),
Visual Studio, PerfView or `dotnet-trace`/`dotnet-gcdump`.

Do not run high-detail JFR, EventPipe traces, heap dumps or GC dumps
continuously. They add overhead and may contain application data.
