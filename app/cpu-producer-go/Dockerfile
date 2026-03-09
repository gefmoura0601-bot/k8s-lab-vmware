FROM golang:1.24-alpine AS builder

WORKDIR /src

COPY go.mod ./
COPY go.sum* ./
RUN go mod download

COPY . .
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -o /out/cpu-producer .

FROM alpine:3.20

RUN adduser -D -u 10001 appuser

WORKDIR /app
COPY --from=builder /out/cpu-producer /app/cpu-producer

USER appuser

ENTRYPOINT ["/app/cpu-producer"]
