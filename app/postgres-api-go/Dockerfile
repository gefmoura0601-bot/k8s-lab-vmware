FROM golang:1.22-alpine AS builder

WORKDIR /src

COPY go.mod go.sum ./
RUN go mod download

COPY main.go ./
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -o /postgres-api .

FROM gcr.io/distroless/static-debian12:nonroot

COPY --from=builder /postgres-api /postgres-api

EXPOSE 8080
USER nonroot:nonroot
ENTRYPOINT ["/postgres-api"]
