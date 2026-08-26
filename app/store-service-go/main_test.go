package main

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
)

const testIdempotencyKey = "3b241101-e2bb-4255-8caf-4136c566a962"
const testLabPAN = "9999990000000006"

type roundTripFunc func(*http.Request) (*http.Response, error)

func (function roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return function(request)
}

type trackingBody struct {
	reader strings.Reader
	reads  atomic.Int32
	closed atomic.Bool
}

func (body *trackingBody) Read(buffer []byte) (int, error) {
	body.reads.Add(1)
	return body.reader.Read(buffer)
}

func (body *trackingBody) Close() error {
	body.closed.Store(true)
	return nil
}

func validCheckoutJSON(extra string) string {
	body := `{
		"productId":"headphones",
		"quantity":2,
		"paymentType":"CREDIT",
		"installments":2,
		"card":{"number":"9999 9900 0000 0006","holderName":"Cliente Virtual","expiryMonth":12,"expiryYear":2099,"cvv":"123"}`
	if extra != "" {
		body += "," + extra
	}
	return body + "}"
}

func checkoutRequestFor(t *testing.T, handler http.Handler, body, key string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(http.MethodPost, "/store/api/checkout", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	if key != "" {
		req.Header.Set("Idempotency-Key", key)
	}
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, req)
	return recorder
}

func TestCheckoutRecalculatesPriceAndSanitizesResponse(t *testing.T) {
	var received map[string]any
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/internal/v1/payments" {
			t.Errorf("unexpected upstream path: %s", r.URL.Path)
		}
		if got := r.Header.Get("Idempotency-Key"); got != testIdempotencyKey {
			t.Errorf("unexpected idempotency key: %s", got)
		}
		decoder := json.NewDecoder(r.Body)
		decoder.UseNumber()
		if err := decoder.Decode(&received); err != nil {
			t.Fatal(err)
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusCreated)
		_, _ = io.WriteString(w, `{"paymentId":"pay-123","status":"CAPTURED","authorizationCode":"A12345","cardType":"CREDIT","last4":"0006","pan":"9999990000000006","cvv":"123"}`)
	}))
	defer upstream.Close()

	app, err := newApplication(upstream.URL, upstream.Client())
	if err != nil {
		t.Fatal(err)
	}
	response := checkoutRequestFor(t, app.handler(), validCheckoutJSON(`"amount":0.01,"merchantId":"attacker"`), testIdempotencyKey)
	if response.Code != http.StatusCreated {
		t.Fatalf("expected 201, got %d: %s", response.Code, response.Body.String())
	}
	if received["merchantId"] != merchantID || received["merchantName"] != "Moura Lab Store" || received["orderId"] != testIdempotencyKey {
		t.Fatalf("server-owned identifiers were not enforced: %#v", received)
	}
	if got := received["amount"].(json.Number).String(); got != "299.80" {
		t.Fatalf("expected server total 299.80, got %s", got)
	}
	if received["currency"] != "BRL" || received["description"] != "Fone Aurora x2" {
		t.Fatalf("unexpected server-owned payment data: %#v", received)
	}
	card, ok := received["card"].(map[string]any)
	if !ok || card["number"] != testLabPAN || card["cvv"] != "123" {
		t.Fatalf("laboratory card was not forwarded in the expected contract: %#v", received["card"])
	}

	responseBody := response.Body.String()
	if strings.Contains(responseBody, testLabPAN) || strings.Contains(responseBody, `"cvv"`) || strings.Contains(responseBody, `"pan"`) {
		t.Fatalf("sensitive card data leaked in response: %s", responseBody)
	}
	var result checkoutResponse
	if err := json.Unmarshal(response.Body.Bytes(), &result); err != nil {
		t.Fatal(err)
	}
	if result.Last4 != "0006" || result.Status != "CAPTURED" || result.Amount != 29980 {
		t.Fatalf("unexpected checkout response: %#v", result)
	}
}

func TestCheckoutValidatesInputBeforeCallingAcquirer(t *testing.T) {
	var calls atomic.Int32
	upstream := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) { calls.Add(1) }))
	defer upstream.Close()
	app, err := newApplication(upstream.URL, upstream.Client())
	if err != nil {
		t.Fatal(err)
	}

	tests := []struct {
		name            string
		key             string
		body            string
		want            int
		messageContains string
	}{
		{name: "missing idempotency key", body: validCheckoutJSON(""), want: http.StatusBadRequest},
		{name: "malformed UUID", key: "not-a-uuid", body: validCheckoutJSON(""), want: http.StatusBadRequest},
		{name: "unknown product", key: testIdempotencyKey, body: strings.Replace(validCheckoutJSON(""), "headphones", "not-found", 1), want: http.StatusUnprocessableEntity},
		{name: "invalid quantity", key: testIdempotencyKey, body: strings.Replace(validCheckoutJSON(""), `"quantity":2`, `"quantity":0`, 1), want: http.StatusUnprocessableEntity},
		{name: "invalid Luhn", key: testIdempotencyKey, body: strings.Replace(validCheckoutJSON(""), "9999 9900 0000 0006", "9999 9900 0000 0005", 1), want: http.StatusUnprocessableEntity, messageContains: "BIN 999999"},
		{name: "non-lab BIN", key: testIdempotencyKey, body: strings.Replace(validCheckoutJSON(""), "9999 9900 0000 0006", "8888 8800 0000 0008", 1), want: http.StatusUnprocessableEntity},
		{name: "wrong PAN length", key: testIdempotencyKey, body: strings.Replace(validCheckoutJSON(""), "9999 9900 0000 0006", "9999 9900 0000 006", 1), want: http.StatusUnprocessableEntity},
		{name: "four-digit CVV", key: testIdempotencyKey, body: strings.Replace(validCheckoutJSON(""), `"cvv":"123"`, `"cvv":"1234"`, 1), want: http.StatusUnprocessableEntity, messageContains: "exatamente 3 dígitos"},
		{name: "non-numeric CVV", key: testIdempotencyKey, body: strings.Replace(validCheckoutJSON(""), `"cvv":"123"`, `"cvv":"12a"`, 1), want: http.StatusUnprocessableEntity, messageContains: "exatamente 3 dígitos"},
		{name: "debit installments", key: testIdempotencyKey, body: strings.Replace(validCheckoutJSON(""), `"paymentType":"CREDIT"`, `"paymentType":"DEBIT"`, 1), want: http.StatusUnprocessableEntity},
		{name: "multiple JSON values", key: testIdempotencyKey, body: validCheckoutJSON("") + `{}`, want: http.StatusBadRequest},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			response := checkoutRequestFor(t, app.handler(), tt.body, tt.key)
			if response.Code != tt.want {
				t.Fatalf("expected %d, got %d: %s", tt.want, response.Code, response.Body.String())
			}
			if tt.messageContains != "" && !strings.Contains(response.Body.String(), tt.messageContains) {
				t.Fatalf("expected response to contain %q: %s", tt.messageContains, response.Body.String())
			}
		})
	}
	if calls.Load() != 0 {
		t.Fatalf("acquirer received %d invalid requests", calls.Load())
	}
}

func TestCheckoutProxiesDeclineAndMasksFromOriginalCard(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusUnprocessableEntity)
		_, _ = io.WriteString(w, `{"paymentId":"pay-declined","status":"DECLINED","declineCode":"INSUFFICIENT_LIMIT","cardType":"CREDIT","last4":"9999"}`)
	}))
	defer upstream.Close()
	app, err := newApplication(upstream.URL, upstream.Client())
	if err != nil {
		t.Fatal(err)
	}

	response := checkoutRequestFor(t, app.handler(), validCheckoutJSON(""), testIdempotencyKey)
	if response.Code != http.StatusUnprocessableEntity {
		t.Fatalf("expected upstream status 422, got %d", response.Code)
	}
	var result checkoutResponse
	if err := json.Unmarshal(response.Body.Bytes(), &result); err != nil {
		t.Fatal(err)
	}
	if result.Status != "DECLINED" || result.DeclineCode != "INSUFFICIENT_LIMIT" || result.Last4 != "0006" {
		t.Fatalf("unexpected decline response: %#v", result)
	}
}

func TestCheckoutSanitizesIdempotencyConflictWithoutReadingUpstreamBody(t *testing.T) {
	upstreamBody := &trackingBody{
		reader: *strings.NewReader(`{"code":"upstream_secret","message":"PAN 9999990000000006 CVV 123"}`),
	}
	client := &http.Client{Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
		return &http.Response{
			StatusCode: http.StatusConflict,
			Status:     "409 Conflict",
			Header:     make(http.Header),
			Body:       upstreamBody,
			Request:    request,
		}, nil
	})}
	app, err := newApplication("http://acquirer.test", client)
	if err != nil {
		t.Fatal(err)
	}

	response := checkoutRequestFor(t, app.handler(), validCheckoutJSON(""), testIdempotencyKey)
	if response.Code != http.StatusConflict {
		t.Fatalf("expected 409, got %d: %s", response.Code, response.Body.String())
	}
	if upstreamBody.reads.Load() != 0 {
		t.Fatalf("upstream conflict body was read %d times", upstreamBody.reads.Load())
	}
	if !upstreamBody.closed.Load() {
		t.Fatal("upstream conflict body was not closed")
	}
	var result map[string]string
	if err := json.Unmarshal(response.Body.Bytes(), &result); err != nil {
		t.Fatal(err)
	}
	if len(result) != 2 || result["code"] != "IDEMPOTENCY_CONFLICT" ||
		result["message"] != "A chave idempotente já foi usada em outro pagamento" {
		t.Fatalf("unexpected sanitized conflict: %#v", result)
	}
	if strings.Contains(response.Body.String(), testLabPAN) || strings.Contains(response.Body.String(), "upstream_secret") {
		t.Fatalf("upstream conflict details leaked: %s", response.Body.String())
	}
}

func TestEmbeddedCheckoutPreservesConflictKeyAndDisablesAutocomplete(t *testing.T) {
	index, err := staticFiles.ReadFile("static/index.html")
	if err != nil {
		t.Fatal(err)
	}
	markup := string(index)
	if strings.Contains(markup, `autocomplete="cc-`) || strings.Count(markup, `autocomplete="off"`) < 6 {
		t.Fatal("card form must disable browser card autocomplete")
	}
	if !strings.Contains(markup, "999999") {
		t.Fatal("card form must explain the laboratory BIN")
	}
	if !strings.Contains(markup, `name="cvv"`) || !strings.Contains(markup, `maxlength="3"`) ||
		!strings.Contains(markup, `pattern="[0-9]{3}"`) {
		t.Fatal("card form must require an exactly three-digit CVV")
	}

	scriptFile, err := staticFiles.ReadFile("static/app.js")
	if err != nil {
		t.Fatal(err)
	}
	script := string(scriptFile)
	if !strings.Contains(script, "validLabCardNumber") || !strings.Contains(script, `startsWith("999999")`) {
		t.Fatal("browser must validate laboratory card numbers before checkout")
	}
	conflictStart := strings.Index(script, "if (response.status === 409)")
	nextResponseBranch := strings.Index(script, "if (!response.ok && !result.status)")
	if conflictStart < 0 || nextResponseBranch <= conflictStart {
		t.Fatal("explicit conflict branch was not found")
	}
	conflictBranch := script[conflictStart:nextResponseBranch]
	if !strings.Contains(conflictBranch, "return;") || strings.Contains(conflictBranch, "retryKey = null") {
		t.Fatal("conflict branch must return without resetting the retry key")
	}
}

func TestHealthCatalogAndMetrics(t *testing.T) {
	app, err := newApplication("http://acquirer.test", nil)
	if err != nil {
		t.Fatal(err)
	}
	handler := app.handler()

	for _, path := range []string{"/healthz", "/store/api/catalog", "/store/", "/metrics"} {
		req := httptest.NewRequest(http.MethodGet, path, nil)
		response := httptest.NewRecorder()
		handler.ServeHTTP(response, req)
		if response.Code != http.StatusOK {
			t.Errorf("GET %s returned %d", path, response.Code)
		}
	}

	req := httptest.NewRequest(http.MethodGet, "/metrics", nil)
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, req)
	if !strings.Contains(response.Body.String(), `store_checkout_requests_total{outcome="captured"}`) {
		t.Fatalf("metrics response missing checkout counter: %s", response.Body.String())
	}
}
