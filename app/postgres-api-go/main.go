package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"github.com/lib/pq"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

const (
	dbStartupTimeout      = 5 * time.Second
	dbRequestTimeout      = 3 * time.Second
	serverShutdownTimeout = 15 * time.Second
	maxRequestBodyBytes   = 1 << 20 // 1 MiB
)

type App struct {
	db *sql.DB
}

type User struct {
	ID        int       `json:"id"`
	Name      string    `json:"name"`
	Email     string    `json:"email"`
	CreatedAt time.Time `json:"created_at"`
}

type CreateUserRequest struct {
	Name  string `json:"name"`
	Email string `json:"email"`
}

func main() {
	dbHost := getenvAny([]string{"DB_HOST", "POSTGRES_HOST"}, "postgres.databases.svc.cluster.local")
	dbPort := getenvAny([]string{"DB_PORT", "POSTGRES_PORT"}, "5432")
	dbName := getenvAny([]string{"DB_NAME", "POSTGRES_DB"}, "appdb")
	dbUser := getenvAny([]string{"DB_USER", "POSTGRES_USER"}, "appuser")
	dbPassword := getenvAny([]string{"DB_PASSWORD", "POSTGRES_PASSWORD"}, "")
	dbSSLMode := getenvAny([]string{"DB_SSLMODE", "POSTGRES_SSLMODE"}, "disable")
	listenAddr := normalizeListenAddr(getenvAny([]string{"LISTEN_ADDR", "SERVER_PORT"}, ":8080"))

	if dbPassword == "" {
		log.Fatal("variável de ambiente do segredo do banco não definida")
	}

	dsn := fmt.Sprintf(
		"host=%s port=%s user=%s password=%s dbname=%s sslmode=%s",
		dbHost, dbPort, dbUser, dbPassword, dbName, dbSSLMode,
	)

	db, err := sql.Open("postgres", dsn)
	if err != nil {
		log.Fatalf("erro ao abrir conexão com banco: %v", err)
	}

	db.SetMaxOpenConns(10)
	db.SetMaxIdleConns(5)
	db.SetConnMaxLifetime(30 * time.Minute)
	db.SetConnMaxIdleTime(10 * time.Minute)

	startupCtx, startupCancel := context.WithTimeout(context.Background(), dbStartupTimeout)
	defer startupCancel()

	if err := db.PingContext(startupCtx); err != nil {
		log.Fatalf("erro ao conectar no PostgreSQL: %v", err)
	}

	log.Println("conectado ao PostgreSQL com sucesso")

	shutdownTracing, err := setupTracing(context.Background())
	if err != nil {
		log.Fatalf("erro ao iniciar OpenTelemetry: %v", err)
	}
	defer func() {
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		if err := shutdownTracing(shutdownCtx); err != nil {
			log.Printf("erro ao finalizar OpenTelemetry: %v", err)
		}
	}()

	app := &App{db: db}

	mux := http.NewServeMux()
	mux.Handle("/metrics", promhttp.Handler())
	mux.HandleFunc("/health", app.handleHealth)
	mux.HandleFunc("/healthz", app.handleHealth)
	mux.HandleFunc("/readyz", app.handleReady)
	mux.HandleFunc("/users", app.handleUsers)
	mux.HandleFunc("/", app.handleRoot)

	server := &http.Server{
		Addr:              listenAddr,
		Handler:           tracedHTTPHandler(loggingMiddleware(mux)),
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       10 * time.Second,
		WriteTimeout:      10 * time.Second,
		IdleTimeout:       30 * time.Second,
	}

	serverErrCh := make(chan error, 1)
	go func() {
		log.Printf("api iniciada em %s", listenAddr)
		if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			serverErrCh <- err
		}
	}()

	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)
	defer signal.Stop(sigCh)

	select {
	case err := <-serverErrCh:
		log.Fatalf("erro no servidor HTTP: %v", err)

	case sig := <-sigCh:
		log.Printf("sinal recebido: %s; iniciando shutdown gracioso", sig)

		shutdownCtx, shutdownCancel := context.WithTimeout(context.Background(), serverShutdownTimeout)
		defer shutdownCancel()

		if err := server.Shutdown(shutdownCtx); err != nil {
			log.Printf("erro no shutdown do servidor HTTP: %v", err)
		}

		if err := db.Close(); err != nil {
			log.Printf("erro ao fechar conexão com banco: %v", err)
		}

		log.Println("aplicação encerrada com sucesso")
	}
}

func (a *App) handleRoot(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path != "/" {
		writeJSON(w, http.StatusNotFound, map[string]string{
			"error": "rota não encontrada",
		})
		return
	}

	writeJSON(w, http.StatusOK, map[string]string{
		"message": "postgres-api-go online",
		"service": "postgres-api",
	})
}

func (a *App) handleHealth(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, map[string]string{
		"status":  "ok",
		"message": "postgres-api-go online",
		"service": "postgres-api",
	})
}

func (a *App) handleReady(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithTimeout(r.Context(), dbRequestTimeout)
	defer cancel()

	if err := a.db.PingContext(ctx); err != nil {
		log.Printf("erro no readiness check do banco: %v", err)
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{
			"status": "not ready",
		})
		return
	}

	writeJSON(w, http.StatusOK, map[string]string{
		"status": "ready",
	})
}

func (a *App) handleUsers(w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodGet:
		a.listUsers(w, r)
	case http.MethodPost:
		a.createUser(w, r)
	default:
		writeJSON(w, http.StatusMethodNotAllowed, map[string]string{
			"error": "método não permitido",
		})
	}
}

func (a *App) listUsers(w http.ResponseWriter, r *http.Request) {
	ctx, span := startDBSpan(r.Context(), "select")
	defer span.End()
	r = r.WithContext(ctx)
	ctx, cancel := context.WithTimeout(r.Context(), dbRequestTimeout)
	defer cancel()

	rows, err := a.db.QueryContext(ctx, `
		SELECT id, name, email, created_at
		FROM users
		ORDER BY id
	`)
	if err != nil {
		log.Printf("erro ao listar usuários: %v", err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{
			"error": "erro interno ao consultar usuários",
		})
		return
	}
	defer rows.Close()

	var users []User

	for rows.Next() {
		var u User
		if err := rows.Scan(&u.ID, &u.Name, &u.Email, &u.CreatedAt); err != nil {
			log.Printf("erro ao ler linha de usuários: %v", err)
			writeJSON(w, http.StatusInternalServerError, map[string]string{
				"error": "erro interno ao ler usuários",
			})
			return
		}
		users = append(users, u)
	}

	if err := rows.Err(); err != nil {
		log.Printf("erro ao iterar usuários: %v", err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{
			"error": "erro interno ao iterar usuários",
		})
		return
	}

	writeJSON(w, http.StatusOK, users)
}

func (a *App) createUser(w http.ResponseWriter, r *http.Request) {
	ctx, span := startDBSpan(r.Context(), "insert")
	defer span.End()
	r = r.WithContext(ctx)
	r.Body = http.MaxBytesReader(w, r.Body, maxRequestBodyBytes)
	defer r.Body.Close()

	var req CreateUserRequest
	decoder := json.NewDecoder(r.Body)
	decoder.DisallowUnknownFields()

	if err := decoder.Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{
			"error": "json inválido",
		})
		return
	}

	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		writeJSON(w, http.StatusBadRequest, map[string]string{
			"error": "json deve conter apenas um objeto",
		})
		return
	}

	req.Name = strings.TrimSpace(req.Name)
	req.Email = strings.TrimSpace(req.Email)

	if req.Name == "" || req.Email == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{
			"error": "name e email são obrigatórios",
		})
		return
	}

	ctx, cancel := context.WithTimeout(r.Context(), dbRequestTimeout)
	defer cancel()

	var user User
	err := a.db.QueryRowContext(ctx, `
		INSERT INTO users (name, email)
		VALUES ($1, $2)
		RETURNING id, name, email, created_at
	`, req.Name, req.Email).Scan(&user.ID, &user.Name, &user.Email, &user.CreatedAt)
	if err != nil {
		var pqErr *pq.Error
		if errors.As(err, &pqErr) && pqErr.Code == "23505" {
			log.Printf("conflito ao criar usuário com email %s: %v", req.Email, err)
			writeJSON(w, http.StatusConflict, map[string]string{
				"error": "email já cadastrado",
			})
			return
		}

		log.Printf("erro ao criar usuário: %v", err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{
			"error": "erro interno ao criar usuário",
		})
		return
	}

	writeJSON(w, http.StatusCreated, user)
}

func loggingMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()

		lrw := &loggingResponseWriter{
			ResponseWriter: w,
			statusCode:     http.StatusOK,
		}

		next.ServeHTTP(lrw, r)

		log.Printf(
			`level=info msg="http_request" method=%s path=%s status=%d duration_ms=%d remote_addr=%s user_agent=%q`,
			r.Method,
			r.URL.Path,
			lrw.statusCode,
			time.Since(start).Milliseconds(),
			r.RemoteAddr,
			r.UserAgent(),
		)
	})
}

type loggingResponseWriter struct {
	http.ResponseWriter
	statusCode int
}

func (lrw *loggingResponseWriter) WriteHeader(code int) {
	lrw.statusCode = code
	lrw.ResponseWriter.WriteHeader(code)
}

func writeJSON(w http.ResponseWriter, status int, payload any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(payload)
}

func getenvAny(keys []string, fallback string) string {
	for _, key := range keys {
		value := strings.TrimSpace(os.Getenv(key))
		if value != "" {
			return value
		}
	}
	return fallback
}

func normalizeListenAddr(value string) string {
	value = strings.TrimSpace(value)
	if value == "" {
		return ":8080"
	}
	if strings.HasPrefix(value, ":") {
		return value
	}
	if !strings.Contains(value, ":") {
		return ":" + value
	}
	return value
}
