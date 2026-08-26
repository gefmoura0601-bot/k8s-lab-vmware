package main

import (
	"context"
	"embed"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"log"
	"net/http"
	"net/url"
	"os"
	"os/signal"
	"regexp"
	"strconv"
	"strings"
	"sync/atomic"
	"syscall"
	"time"
)

const (
	merchantID       = "moura-store"
	labCardBIN       = "999999"
	maxCheckoutBytes = 16 << 10
	maxUpstreamBytes = 64 << 10
)

//go:embed static/*
var staticFiles embed.FS

type money int64

func (m money) MarshalJSON() ([]byte, error) {
	value := int64(m)
	sign := ""
	if value < 0 {
		sign = "-"
		value = -value
	}
	return []byte(fmt.Sprintf("%s%d.%02d", sign, value/100, value%100)), nil
}

func (m *money) UnmarshalJSON(data []byte) error {
	value := strings.TrimSpace(string(data))
	negative := strings.HasPrefix(value, "-")
	if negative {
		value = strings.TrimPrefix(value, "-")
	}
	parts := strings.Split(value, ".")
	if len(parts) > 2 || parts[0] == "" || !digitsPattern.MatchString(parts[0]) {
		return errors.New("invalid monetary value")
	}
	fraction := "00"
	if len(parts) == 2 {
		if len(parts[1]) == 0 || len(parts[1]) > 2 || !digitsPattern.MatchString(parts[1]) {
			return errors.New("invalid monetary value")
		}
		fraction = parts[1] + strings.Repeat("0", 2-len(parts[1]))
	}
	units, err := strconv.ParseInt(parts[0], 10, 64)
	if err != nil {
		return errors.New("invalid monetary value")
	}
	cents, err := strconv.ParseInt(fraction, 10, 64)
	if err != nil {
		return errors.New("invalid monetary value")
	}
	total := units*100 + cents
	if negative {
		total = -total
	}
	*m = money(total)
	return nil
}

type product struct {
	ID          string `json:"id"`
	Name        string `json:"name"`
	Description string `json:"description"`
	Icon        string `json:"icon"`
	Price       money  `json:"price"`
}

var catalog = []product{
	{ID: "headphones", Name: "Fone Aurora", Description: "Fone sem fio com cancelamento de ruído", Icon: "◉", Price: 14990},
	{ID: "keyboard", Name: "Teclado Horizonte", Description: "Teclado compacto mecânico para o escritório", Icon: "⌨", Price: 24990},
	{ID: "mug", Name: "Caneca Moura", Description: "Caneca térmica de aço com 450 ml", Icon: "◌", Price: 3990},
}

type cardInput struct {
	Number      string `json:"number"`
	HolderName  string `json:"holderName"`
	ExpiryMonth int    `json:"expiryMonth"`
	ExpiryYear  int    `json:"expiryYear"`
	CVV         string `json:"cvv"`
}

type checkoutRequest struct {
	ProductID    string    `json:"productId"`
	Quantity     int       `json:"quantity"`
	Card         cardInput `json:"card"`
	PaymentType  string    `json:"paymentType"`
	Installments int       `json:"installments"`
}

type acquirerPaymentRequest struct {
	MerchantID   string    `json:"merchantId"`
	MerchantName string    `json:"merchantName"`
	OrderID      string    `json:"orderId"`
	Amount       money     `json:"amount"`
	Currency     string    `json:"currency"`
	Description  string    `json:"description"`
	Card         cardInput `json:"card"`
	PaymentType  string    `json:"paymentType"`
	Installments int       `json:"installments"`
}

type acquirerPaymentResponse struct {
	PaymentID         string `json:"paymentId"`
	Status            string `json:"status"`
	AuthorizationCode string `json:"authorizationCode,omitempty"`
	DeclineCode       string `json:"declineCode,omitempty"`
	CardType          string `json:"cardType"`
	Last4             string `json:"last4"`
}

type checkoutResponse struct {
	OrderID           string `json:"orderId"`
	ProductID         string `json:"productId"`
	ProductName       string `json:"productName"`
	Quantity          int    `json:"quantity"`
	Amount            money  `json:"amount"`
	Currency          string `json:"currency"`
	PaymentID         string `json:"paymentId"`
	Status            string `json:"status"`
	AuthorizationCode string `json:"authorizationCode,omitempty"`
	DeclineCode       string `json:"declineCode,omitempty"`
	CardType          string `json:"cardType"`
	Last4             string `json:"last4"`
}

type counters struct {
	captured atomic.Uint64
	declined atomic.Uint64
	invalid  atomic.Uint64
	errors   atomic.Uint64
}

type application struct {
	acquirerURL string
	client      *http.Client
	metrics     counters
	static      http.Handler
}

var (
	uuidPattern   = regexp.MustCompile(`^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$`)
	digitsPattern = regexp.MustCompile(`^[0-9]+$`)
)

func newApplication(acquirerURL string, client *http.Client) (*application, error) {
	base, err := url.Parse(strings.TrimRight(acquirerURL, "/"))
	if err != nil || (base.Scheme != "http" && base.Scheme != "https") || base.Host == "" {
		return nil, errors.New("ACQUIRER_URL must be an absolute HTTP(S) URL")
	}
	if client == nil {
		client = &http.Client{Timeout: 7 * time.Second}
	}
	assets, err := fs.Sub(staticFiles, "static")
	if err != nil {
		return nil, fmt.Errorf("load embedded store assets: %w", err)
	}
	return &application{
		acquirerURL: strings.TrimRight(base.String(), "/"),
		client:      client,
		static:      http.StripPrefix("/store/", http.FileServer(http.FS(assets))),
	}, nil
}

func (a *application) handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", a.health)
	mux.HandleFunc("GET /metrics", a.prometheus)
	mux.HandleFunc("GET /store", func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, "/store/", http.StatusPermanentRedirect)
	})
	mux.HandleFunc("GET /store/api/catalog", a.getCatalog)
	mux.HandleFunc("POST /store/api/checkout", a.checkout)
	mux.Handle("GET /store/", a.static)
	return securityHeaders(mux)
}

func securityHeaders(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Header().Set("X-Frame-Options", "DENY")
		w.Header().Set("Referrer-Policy", "no-referrer")
		w.Header().Set("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
		next.ServeHTTP(w, r)
	})
}

func (a *application) health(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "text/plain; charset=utf-8")
	w.WriteHeader(http.StatusOK)
	_, _ = io.WriteString(w, "ok\n")
}

func (a *application) prometheus(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
	_, _ = fmt.Fprintf(w, "# HELP store_checkout_requests_total Checkout requests handled by outcome.\n")
	_, _ = fmt.Fprintf(w, "# TYPE store_checkout_requests_total counter\n")
	_, _ = fmt.Fprintf(w, "store_checkout_requests_total{outcome=\"captured\"} %d\n", a.metrics.captured.Load())
	_, _ = fmt.Fprintf(w, "store_checkout_requests_total{outcome=\"declined\"} %d\n", a.metrics.declined.Load())
	_, _ = fmt.Fprintf(w, "store_checkout_requests_total{outcome=\"invalid\"} %d\n", a.metrics.invalid.Load())
	_, _ = fmt.Fprintf(w, "store_checkout_requests_total{outcome=\"error\"} %d\n", a.metrics.errors.Load())
}

func (a *application) getCatalog(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Cache-Control", "public, max-age=300")
	writeJSON(w, http.StatusOK, map[string]any{"currency": "BRL", "products": catalog})
}

func (a *application) checkout(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	idempotencyKey := strings.TrimSpace(r.Header.Get("Idempotency-Key"))
	if !uuidPattern.MatchString(idempotencyKey) {
		a.metrics.invalid.Add(1)
		writeError(w, http.StatusBadRequest, "INVALID_IDEMPOTENCY_KEY", "Idempotency-Key deve ser um UUID válido")
		return
	}

	r.Body = http.MaxBytesReader(w, r.Body, maxCheckoutBytes)
	decoder := json.NewDecoder(r.Body)
	var input checkoutRequest
	if err := decoder.Decode(&input); err != nil {
		a.metrics.invalid.Add(1)
		writeError(w, http.StatusBadRequest, "INVALID_JSON", "Corpo JSON inválido")
		return
	}
	if err := ensureJSONEOF(decoder); err != nil {
		a.metrics.invalid.Add(1)
		writeError(w, http.StatusBadRequest, "INVALID_JSON", "Envie somente um objeto JSON")
		return
	}

	selected, normalized, err := validateCheckout(input)
	if err != nil {
		a.metrics.invalid.Add(1)
		writeError(w, http.StatusUnprocessableEntity, "INVALID_CHECKOUT", err.Error())
		return
	}

	total := money(int64(normalized.Quantity) * int64(selected.Price))
	upstreamPayload := acquirerPaymentRequest{
		MerchantID:   merchantID,
		MerchantName: "Moura Lab Store",
		OrderID:      strings.ToLower(idempotencyKey),
		Amount:       total,
		Currency:     "BRL",
		Description:  fmt.Sprintf("%s x%d", selected.Name, normalized.Quantity),
		Card:         normalized.Card,
		PaymentType:  normalized.PaymentType,
		Installments: normalized.Installments,
	}
	body, err := json.Marshal(upstreamPayload)
	if err != nil {
		a.metrics.errors.Add(1)
		writeError(w, http.StatusInternalServerError, "INTERNAL_ERROR", "Não foi possível preparar o pagamento")
		return
	}

	upstreamRequest, err := http.NewRequestWithContext(r.Context(), http.MethodPost, a.acquirerURL+"/internal/v1/payments", strings.NewReader(string(body)))
	if err != nil {
		a.metrics.errors.Add(1)
		writeError(w, http.StatusBadGateway, "ACQUIRER_UNAVAILABLE", "Adquirência indisponível")
		return
	}
	upstreamRequest.Header.Set("Content-Type", "application/json")
	upstreamRequest.Header.Set("Accept", "application/json")
	upstreamRequest.Header.Set("Idempotency-Key", strings.ToLower(idempotencyKey))

	upstreamResponse, err := a.client.Do(upstreamRequest)
	if err != nil {
		a.metrics.errors.Add(1)
		writeError(w, http.StatusBadGateway, "ACQUIRER_UNAVAILABLE", "Adquirência indisponível")
		return
	}
	defer upstreamResponse.Body.Close()
	if upstreamResponse.StatusCode == http.StatusConflict {
		a.metrics.errors.Add(1)
		writeError(w, http.StatusConflict, "IDEMPOTENCY_CONFLICT", "A chave idempotente já foi usada em outro pagamento")
		return
	}
	if upstreamResponse.StatusCode < 200 || upstreamResponse.StatusCode >= 500 {
		a.metrics.errors.Add(1)
		writeError(w, http.StatusBadGateway, "ACQUIRER_UNAVAILABLE", "Adquirência indisponível")
		return
	}

	var payment acquirerPaymentResponse
	responseDecoder := json.NewDecoder(io.LimitReader(upstreamResponse.Body, maxUpstreamBytes))
	if err := responseDecoder.Decode(&payment); err != nil {
		a.metrics.errors.Add(1)
		writeError(w, http.StatusBadGateway, "INVALID_ACQUIRER_RESPONSE", "Resposta inválida da adquirência")
		return
	}
	payment.Status = strings.ToUpper(strings.TrimSpace(payment.Status))
	if payment.Status != "CAPTURED" && payment.Status != "DECLINED" {
		a.metrics.errors.Add(1)
		writeError(w, http.StatusBadGateway, "INVALID_ACQUIRER_RESPONSE", "Status inválido da adquirência")
		return
	}
	if payment.PaymentID == "" {
		a.metrics.errors.Add(1)
		writeError(w, http.StatusBadGateway, "INVALID_ACQUIRER_RESPONSE", "Pagamento sem identificador")
		return
	}

	if payment.Status == "CAPTURED" {
		a.metrics.captured.Add(1)
	} else {
		a.metrics.declined.Add(1)
	}
	writeJSON(w, upstreamResponse.StatusCode, checkoutResponse{
		OrderID:           strings.ToLower(idempotencyKey),
		ProductID:         selected.ID,
		ProductName:       selected.Name,
		Quantity:          normalized.Quantity,
		Amount:            total,
		Currency:          "BRL",
		PaymentID:         payment.PaymentID,
		Status:            payment.Status,
		AuthorizationCode: payment.AuthorizationCode,
		DeclineCode:       payment.DeclineCode,
		CardType:          payment.CardType,
		Last4:             normalized.Card.Number[len(normalized.Card.Number)-4:],
	})
}

func ensureJSONEOF(decoder *json.Decoder) error {
	var extra any
	err := decoder.Decode(&extra)
	if errors.Is(err, io.EOF) {
		return nil
	}
	if err == nil {
		return errors.New("extra JSON value")
	}
	return err
}

func validateCheckout(input checkoutRequest) (product, checkoutRequest, error) {
	selected, ok := findProduct(strings.TrimSpace(input.ProductID))
	if !ok {
		return product{}, input, errors.New("produto não encontrado")
	}
	if input.Quantity < 1 || input.Quantity > 10 {
		return product{}, input, errors.New("a quantidade deve estar entre 1 e 10")
	}

	input.Card.Number = strings.NewReplacer(" ", "", "-", "").Replace(input.Card.Number)
	input.Card.HolderName = strings.TrimSpace(input.Card.HolderName)
	input.Card.CVV = strings.TrimSpace(input.Card.CVV)
	if len(input.Card.Number) != 16 || !strings.HasPrefix(input.Card.Number, labCardBIN) ||
		!digitsPattern.MatchString(input.Card.Number) || !validLuhn(input.Card.Number) {
		return product{}, input, errors.New("use um cartão virtual do laboratório com BIN 999999")
	}
	if len(input.Card.HolderName) < 2 || len(input.Card.HolderName) > 100 {
		return product{}, input, errors.New("nome do titular inválido")
	}
	if input.Card.ExpiryMonth < 1 || input.Card.ExpiryMonth > 12 {
		return product{}, input, errors.New("mês de validade inválido")
	}
	now := time.Now().UTC()
	if input.Card.ExpiryYear < now.Year() || (input.Card.ExpiryYear == now.Year() && input.Card.ExpiryMonth < int(now.Month())) {
		return product{}, input, errors.New("cartão expirado")
	}
	if len(input.Card.CVV) != 3 || !digitsPattern.MatchString(input.Card.CVV) {
		return product{}, input, errors.New("CVV deve conter exatamente 3 dígitos")
	}

	input.PaymentType = strings.ToUpper(strings.TrimSpace(input.PaymentType))
	if input.PaymentType != "DEBIT" && input.PaymentType != "CREDIT" {
		return product{}, input, errors.New("paymentType deve ser DEBIT ou CREDIT")
	}
	if input.Installments == 0 {
		input.Installments = 1
	}
	if input.PaymentType == "DEBIT" && input.Installments != 1 {
		return product{}, input, errors.New("débito aceita somente uma parcela")
	}
	if input.PaymentType == "CREDIT" && (input.Installments < 1 || input.Installments > 12) {
		return product{}, input, errors.New("crédito aceita de 1 a 12 parcelas")
	}
	return selected, input, nil
}

func findProduct(id string) (product, bool) {
	for _, item := range catalog {
		if item.ID == id {
			return item, true
		}
	}
	return product{}, false
}

func validLuhn(number string) bool {
	sum := 0
	double := false
	for index := len(number) - 1; index >= 0; index-- {
		digit := int(number[index] - '0')
		if double {
			digit *= 2
			if digit > 9 {
				digit -= 9
			}
		}
		sum += digit
		double = !double
	}
	return sum%10 == 0
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}

func writeError(w http.ResponseWriter, status int, code, message string) {
	writeJSON(w, status, map[string]string{"code": code, "message": message})
}

func main() {
	acquirerURL := strings.TrimSpace(os.Getenv("ACQUIRER_URL"))
	if acquirerURL == "" {
		log.Fatal("ACQUIRER_URL is required")
	}
	app, err := newApplication(acquirerURL, nil)
	if err != nil {
		log.Fatal(err)
	}
	port := strings.TrimSpace(os.Getenv("PORT"))
	if port == "" {
		port = "8080"
	}
	if _, err := strconv.Atoi(port); err != nil {
		log.Fatal("PORT must be numeric")
	}

	server := &http.Server{
		Addr:              ":" + port,
		Handler:           app.handler(),
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       10 * time.Second,
		WriteTimeout:      15 * time.Second,
		IdleTimeout:       60 * time.Second,
	}

	shutdownContext, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	go func() {
		<-shutdownContext.Done()
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		if err := server.Shutdown(ctx); err != nil {
			log.Printf("graceful shutdown failed: %v", err)
		}
	}()

	log.Printf("Moura Store listening on %s", server.Addr)
	if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		log.Fatal(err)
	}
}
