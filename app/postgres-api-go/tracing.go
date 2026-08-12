package main

import (
	"context"
	"log"
	"net/http"
	"os"

	"go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracegrpc"
	"go.opentelemetry.io/otel/propagation"
	"go.opentelemetry.io/otel/sdk/resource"
	tracesdk "go.opentelemetry.io/otel/sdk/trace"
	"go.opentelemetry.io/otel/trace"
)

func setupTracing(ctx context.Context) (func(context.Context) error, error) {
	endpoint := os.Getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
	if endpoint == "" {
		log.Print("OpenTelemetry desabilitado: OTEL_EXPORTER_OTLP_ENDPOINT vazio")
		return func(context.Context) error { return nil }, nil
	}

	exporter, err := otlptracegrpc.New(ctx, otlptracegrpc.WithEndpoint(endpoint), otlptracegrpc.WithInsecure())
	if err != nil {
		return nil, err
	}
	res, err := resource.Merge(resource.Default(), resource.NewWithAttributes("",
		attribute.String("service.name", getenvAny([]string{"OTEL_SERVICE_NAME"}, "postgres-api")),
		attribute.String("service.version", releaseVersion),
		attribute.String("deployment.environment", "lab"),
	))
	if err != nil {
		return nil, err
	}
	provider := tracesdk.NewTracerProvider(
		tracesdk.WithBatcher(exporter),
		tracesdk.WithResource(res),
		tracesdk.WithSampler(tracesdk.ParentBased(tracesdk.AlwaysSample())),
	)
	otel.SetTracerProvider(provider)
	otel.SetTextMapPropagator(propagation.NewCompositeTextMapPropagator(propagation.TraceContext{}, propagation.Baggage{}))
	return provider.Shutdown, nil
}

func startDBSpan(ctx context.Context, operation string) (context.Context, trace.Span) {
	return otel.Tracer("postgres-api/database").Start(ctx, "postgresql.users."+operation,
		trace.WithAttributes(
			attribute.String("db.system", "postgresql"),
			attribute.String("db.namespace", "appdb"),
			attribute.String("db.operation.name", operation),
		),
	)
}

func tracedHTTPHandler(next http.Handler) http.Handler {
	return otelhttp.NewHandler(next, "postgres-api.http")
}
