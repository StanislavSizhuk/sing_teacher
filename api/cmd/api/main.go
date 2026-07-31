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
	"ai-vocal-coach/api/internal/media"
	"ai-vocal-coach/api/internal/oauth"
	"ai-vocal-coach/api/internal/queue"
	"ai-vocal-coach/api/internal/repository/postgres"
	"ai-vocal-coach/api/internal/repository/redisrepo"
	"ai-vocal-coach/api/internal/security"
	"ai-vocal-coach/api/internal/service/analysis"
	"ai-vocal-coach/api/internal/service/auth"
	"ai-vocal-coach/api/internal/service/progress"
	"ai-vocal-coach/api/internal/service/song"
	"ai-vocal-coach/api/internal/storage"
	"ai-vocal-coach/api/internal/sysproc"
	httptransport "ai-vocal-coach/api/internal/transport/http"
	"ai-vocal-coach/api/internal/transport/ws"
	"ai-vocal-coach/api/internal/youtube"
	"ai-vocal-coach/api/migrations"
)

// cleanupInterval is how often the expired-unverified-account sweep runs (FR-05, spec 9).
const cleanupInterval = time.Hour

// shutdownGrace bounds how long in-flight requests get to finish once a
// termination signal arrives.
const shutdownGrace = 20 * time.Second

// audioStorageDir is the shared volume songs and recordings are canonicalized
// into (spec 5.2 "audio-tmp"), mounted by deploy/docker-compose*.yml. It is a
// fixed container path, not operator config -- the E3 worker reads from
// the exact same path.
const audioStorageDir = "/data/audio-tmp"

// audioSweepInterval is how often the orphan sweep runs.
const audioSweepInterval = time.Minute

// orphanedAudioMaxAge bounds the orphan-file safety net, not FR-43's "<=5
// min after processing ends" itself -- the E3 worker now deletes a
// recording/song file precisely when it's done with it
// (queue/handler.py's AnalysisJobHandler._cleanup), immediately satisfying
// FR-43 with room to spare. This sweep only catches what that can't: a
// file written but never enqueued (a crash between upload and the
// analysis row's insert), or one whose worker died before cleanup ran and
// was never retried. QUEUE_MAX_LENGTH (20) queued jobs at a worst-case
// per-job time (spec 6.2's stage timeouts sum to ~15 min) can leave a
// legitimately-still-needed file waiting far longer than AUDIO_TTL_SECONDS
// (5 min) -- reusing that value here would delete audio out from under a
// job still waiting its turn, not just orphans.
const orphanedAudioMaxAge = 24 * time.Hour

// ffmpegPath, ffprobePath and ytDlpPath are resolved via PATH; the runtime
// image (deploy/Dockerfile) installs all three (spec 11.3).
const (
	ffmpegPath  = "ffmpeg"
	ffprobePath = "ffprobe"
	ytDlpPath   = "yt-dlp"
)

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

	if err := checkMediaBinaries(cfg); err != nil {
		return fmt.Errorf("check media binaries: %w", err)
	}

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

	files, err := storage.NewFileStore(audioStorageDir)
	if err != nil {
		return fmt.Errorf("open audio storage: %w", err)
	}
	go runAudioSweepTicker(ctx, logger, files, orphanedAudioMaxAge)

	analysesQueue := queue.NewProducer(redisClient, queue.AnalysesStreamName, queue.AnalysesGroupName)
	if err := analysesQueue.EnsureGroup(ctx); err != nil {
		return fmt.Errorf("prepare analyses:run queue: %w", err)
	}
	songsPrepQueue := queue.NewProducer(redisClient, queue.SongsPrepStreamName, queue.SongsPrepGroupName)
	if err := songsPrepQueue.EnsureGroup(ctx); err != nil {
		return fmt.Errorf("prepare songs:prep queue: %w", err)
	}

	songSvc, analysisSvc := buildSongAndAnalysisServices(cfg, pool, redisClient, files, analysesQueue, songsPrepQueue)
	progressSvc := progress.NewService(postgres.NewProgressRepository(pool))
	hub := ws.NewHub()
	accessParser := security.NewJWTIssuer(cfg.Auth.JWTSecret, cfg.Auth.AccessTokenTTL)
	wsHandler := ws.NewHandler(hub, analysisSvc, accessParser, cfg.CORSOrigin)
	go relayWorkerEvents(ctx, redisClient, hub, logger)

	handler := httptransport.NewHandler(svc, logger, cfg.CookieSecure, cfg.AppBaseURL.String(),
		cfg.Auth.AccessTokenTTL, cfg.Auth.RefreshTokenTTL)
	health := httptransport.NewHealthHandler(pool, redisClient)
	router := httptransport.NewRouter(httptransport.RouterDeps{
		Auth:         handler,
		Health:       health,
		Song:         httptransport.NewSongHandler(songSvc, logger, cfg.Limits.MaxUploadBytes),
		Analysis:     httptransport.NewAnalysisHandler(analysisSvc, hub, logger, cfg.Limits.MaxUploadBytes),
		Progress:     httptransport.NewProgressHandler(progressSvc, logger),
		WS:           wsHandler.ServeAnalysis,
		Logger:       logger,
		CORSOrigin:   cfg.CORSOrigin,
		AccessParser: accessParser,
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

// checkMediaBinaries fails fast if ffmpeg/ffprobe are missing, and yt-dlp
// too when YouTube import is enabled -- instead of only discovering it on
// the first upload (spec 12.1: fail fast with a clear message).
func checkMediaBinaries(cfg *config.Config) error {
	if err := media.CheckBinaries(ffmpegPath, ffprobePath); err != nil {
		return err
	}
	if cfg.Features.YouTubeImport {
		if err := youtube.CheckBinary(ytDlpPath); err != nil {
			return err
		}
	}
	return nil
}

// buildSongAndAnalysisServices wires the E2 song-ingestion and
// analysis-queue services to their Postgres/Redis/filesystem/exec dependencies.
func buildSongAndAnalysisServices(
	cfg *config.Config,
	pool *pgxpool.Pool,
	redisClient *redis.Client,
	files *storage.FileStore,
	analysesQueue *queue.Producer,
	songsPrepQueue *queue.Producer,
) (*song.Service, *analysis.Service) {
	runner := sysproc.NewExecRunner()
	processor := media.NewProcessor(runner, ffmpegPath, ffprobePath)
	ytClient := youtube.NewClient(runner, ytDlpPath)

	songRepo := postgres.NewSongRepository(pool)
	songSvc := song.NewService(songRepo, processor, files, ytClient, songsPrepQueue,
		cfg.Limits.MaxUploadBytes, cfg.Limits.MaxAudioSeconds, cfg.Limits.QueueMaxLength, cfg.Features.YouTubeImport)

	analysisRepo := postgres.NewAnalysisRepository(pool)
	rateLimiter := redisrepo.NewAnalysisRateLimiter(redisClient, cfg.Limits.UserAnalysesPerHour, time.Hour)
	analysisSvc := analysis.NewService(analysisRepo, songRepo, processor, files, rateLimiter, analysesQueue,
		cfg.Limits.MaxUploadBytes, cfg.Limits.MaxAudioSeconds, cfg.Limits.QueueMaxLength)

	return songSvc, analysisSvc
}

// relayWorkerEvents forwards the E3 worker's live stage/done/failed events
// (spec 8.3, ADR-0010) into the WS hub until ctx is canceled. Runs for the
// life of the process; a relay error is logged, never fatal -- REST stays
// the fallback of record regardless (spec 8.3).
func relayWorkerEvents(ctx context.Context, redisClient *redis.Client, hub *ws.Hub, logger *slog.Logger) {
	if err := queue.RelayEvents(ctx, redisClient, hub, logger); err != nil && !errors.Is(err, context.Canceled) {
		logger.Error("worker event relay stopped", "error", err.Error())
	}
}

// runAudioSweepTicker drives the orphaned-file safety net (see
// orphanedAudioMaxAge) until ctx is canceled. FR-43's actual retention
// window is enforced by the E3 worker deleting a file precisely when it's
// done with it, not by this sweep.
func runAudioSweepTicker(
	ctx context.Context, logger *slog.Logger, files *storage.FileStore, maxAge time.Duration,
) {
	ticker := time.NewTicker(audioSweepInterval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			removed, err := files.Sweep(maxAge)
			if err != nil {
				logger.Error("audio sweep failed", "error", err.Error())
				continue
			}
			if removed > 0 {
				logger.Info("swept stale audio files", "count", removed)
			}
		}
	}
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
