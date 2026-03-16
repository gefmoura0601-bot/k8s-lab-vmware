FROM golang:1.24 AS builder

WORKDIR /src

COPY go.mod go.sum ./
RUN go mod download

COPY . .

RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -o /out/postgres-api-go .

FROM gcr.io/distroless/static-debian12

WORKDIR /app

COPY --from=builder /out/postgres-api-go /app/postgres-api-go

EXPOSE 8080

USER nonroot:nonroot

ENTRYPOINT ["/app/postgres-api-go"]
