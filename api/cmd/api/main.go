// Command api runs the AI Vocal Coach REST API: it applies pending database
// migrations, then serves auth and account endpoints until terminated.
package main

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	_ "github.com/jackc/pgx/v5/stdlib" // registers the "pgx" database/sql driver, used only for goose
	"github.com/pressly/goose/v3"
	"github.com/redis/go-redis/v9"

	"ai-vocal-coach/api/internal/config"
	"ai-vocal-coach/api/internal/mailer"
	"ai-vocal-coach/api/internal/oauth"
	"ai-vocal-coach/api/internal/repository/postgres"
	"ai-vocal-coach/api/internal/repository/redisrepo"
	"ai-vocal-coach/api/internal/security"
	"ai-vocal-coach/api/internal/service/auth"
	httptransport "ai-vocal-coach/api/internal/transport/http"
	"ai-vocal-coach/api/migrations"
)

// cleanupInterval is how often the expired-unverified-account sweep runs (FR-05, spec 9).
const cleanupInterval = time.Hour

// shutdownGrace bounds how long in-flight requests get to finish once a
// termination signal arrives.
const shutdownGrace = 20 * time.Second

func main() {
	// distroless has no shell, so Docker's HEALTHCHECK runs this binary
	// against itself instead of a curl/wget one-liner (deploy/docker-compose.yml).
	if len(os.Args) > 1 && os.Args[1] == "healthcheck" {
		os.Exit(runHealthcheck())
	}

	cfg, err := config.Load()
	if err != nil {
		fmt.Fprintln(os.Stderr, "config:", err)
		os.Exit(1)
	}

	logger := newLogger(cfg.LogLevel)
	if err := run(cfg, logger); err != nil {
		logger.Error("fatal", "error", err.Error())
		os.Exit(1)
	}
}

// runHealthcheck asks the already-running instance of this process whether
// it considers itself ready, via a plain loopback HTTP call. It deliberately
// skips config.Load() (and thus every required-secret check): a healthcheck
// must not fail the container just because, say, SMTP_FROM is unset.
func runHealthcheck() int {
	port := os.Getenv("API_HTTP_PORT")
	if port == "" {
		port = "8080"
	}
	client := http.Client{Timeout: 3 * time.Second}
	// Loopback call to our own process; port is trusted operator config, not request input.
	resp, err := client.Get("http://127.0.0.1:" + port + "/readyz") // #nosec G704
	if err != nil {
		return 1
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode != http.StatusOK {
		return 1
	}
	return 0
}

func newLogger(level slog.Level) *slog.Logger {
	opts := &slog.HandlerOptions{
		Level: level,
		ReplaceAttr: func(_ []string, a slog.Attr) slog.Attr {
			if a.Key == slog.TimeKey {
				a.Key = "ts"
			}
			return a
		},
	}
	return slog.New(slog.NewJSONHandler(os.Stdout, opts))
}

func run(cfg *config.Config, logger *slog.Logger) error {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	if err := applyMigrations(cfg); err != nil {
		return fmt.Errorf("apply migrations: %w", err)
	}
	logger.Info("migrations applied")

	pool, err := pgxpool.New(ctx, cfg.Postgres.DSN())
	if err != nil {
		return fmt.Errorf("connect to postgres: %w", err)
	}
	defer pool.Close()
	if err := pool.Ping(ctx); err != nil {
		return fmt.Errorf("ping postgres: %w", err)
	}

	redisClient := redis.NewClient(&redis.Options{
		Addr:     cfg.Redis.Addr(),
		Password: cfg.Redis.Password,
		DB:       cfg.Redis.DB,
	})
	defer func() { _ = redisClient.Close() }()
	if err := redisClient.Ping(ctx).Err(); err != nil {
		return fmt.Errorf("ping redis: %w", err)
	}

	svc, err := buildAuthService(cfg, pool, redisClient)
	if err != nil {
		return fmt.Errorf("build auth service: %w", err)
	}

	handler := httptransport.NewHandler(svc, logger, cfg.CookieSecure, cfg.AppBaseURL.String(),
		cfg.Auth.AccessTokenTTL, cfg.Auth.RefreshTokenTTL)
	health := httptransport.NewHealthHandler(pool, redisClient)
	router := httptransport.NewRouter(httptransport.RouterDeps{
		Auth:         handler,
		Health:       health,
		Logger:       logger,
		CORSOrigin:   cfg.CORSOrigin,
		AccessParser: security.NewJWTIssuer(cfg.Auth.JWTSecret, cfg.Auth.AccessTokenTTL),
	})

	server := &http.Server{
		Addr:              fmt.Sprintf(":%d", cfg.HTTPPort),
		Handler:           router,
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       10 * time.Second,
		WriteTimeout:      10 * time.Second,
		IdleTimeout:       60 * time.Second,
	}

	go runCleanupTicker(ctx, logger, svc)

	serveErr := make(chan error, 1)
	go func() {
		logger.Info("listening", "addr", server.Addr)
		if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			serveErr <- err
			return
		}
		serveErr <- nil
	}()

	select {
	case err := <-serveErr:
		if err != nil {
			return fmt.Errorf("serve: %w", err)
		}
	case <-ctx.Done():
		logger.Info("shutting down")
		shutdownCtx, cancel := context.WithTimeout(context.Background(), shutdownGrace)
		defer cancel()
		if err := server.Shutdown(shutdownCtx); err != nil {
			return fmt.Errorf("graceful shutdown: %w", err)
		}
	}
	return nil
}

// applyMigrations runs every pending goose migration embedded in the binary,
// so "docker compose up" alone brings the schema up to date (spec 18/E1).
func applyMigrations(cfg *config.Config) error {
	db, err := sql.Open("pgx", cfg.Postgres.DSN())
	if err != nil {
		return fmt.Errorf("open migration connection: %w", err)
	}
	defer func() { _ = db.Close() }()

	goose.SetBaseFS(migrations.FS)
	if err := goose.SetDialect("postgres"); err != nil {
		return fmt.Errorf("set goose dialect: %w", err)
	}
	if err := goose.Up(db, "."); err != nil {
		return fmt.Errorf("goose up: %w", err)
	}
	return nil
}

func buildAuthService(cfg *config.Config, pool *pgxpool.Pool, redisClient *redis.Client) (*auth.Service, error) {
	hasher, err := security.NewPasswordHasher()
	if err != nil {
		return nil, fmt.Errorf("build password hasher: %w", err)
	}

	users := postgres.NewUserRepository(pool)
	tokens := redisrepo.NewRefreshTokenStore(redisClient, cfg.Auth.RefreshTokenTTL)
	loginThrottle := redisrepo.NewLoginThrottle(redisClient)
	verifyThrottle := redisrepo.NewVerificationThrottle(redisClient)

	jwtIssuer := security.NewJWTIssuer(cfg.Auth.JWTSecret, cfg.Auth.AccessTokenTTL)
	mailSender := mailer.NewSMTPMailer(cfg.SMTP.Host, cfg.SMTP.Port, cfg.SMTP.User, cfg.SMTP.Password, cfg.SMTP.From)
	googleVerifier := oauth.NewGoogleVerifier(cfg.Auth.GoogleClientID, cfg.Auth.GoogleClientSecret,
		config.GoogleRedirectURL(cfg.AppBaseURL))

	return auth.NewService(users, tokens, mailSender, hasher, jwtIssuer, loginThrottle, verifyThrottle,
		googleVerifier, auth.RealClock{}), nil
}

// runCleanupTicker drives the hourly sweep for expired-unverified accounts
// (FR-05) until ctx is canceled.
func runCleanupTicker(ctx context.Context, logger *slog.Logger, svc *auth.Service) {
	ticker := time.NewTicker(cleanupInterval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			count, err := svc.CleanupExpiredUnverifiedAccounts(ctx)
			if err != nil {
				logger.Error("cleanup expired unverified accounts failed", "error", err.Error())
				continue
			}
			if count > 0 {
				logger.Info("cleaned up expired unverified accounts", "count", count)
			}
		}
	}
}
