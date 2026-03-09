FROM golang:1.24-alpine AS builder

WORKDIR /src

COPY go.mod ./
RUN go mod download

COPY main.go ./
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -o /out/cpu-worker main.go

FROM alpine:3.20

RUN adduser -D -u 10001 appuser

WORKDIR /app
COPY --from=builder /out/cpu-worker /app/cpu-worker

USER appuser

ENTRYPOINT ["/app/cpu-worker"]
