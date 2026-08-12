// Package config loads and validates the process configuration from the
// environment. Loading fails fast with every problem listed at once, so a
// misconfigured deployment never starts silently wrong (spec 12.1).
package config

import (
	"fmt"
	"log/slog"
	"net/url"
	"os"
	"strconv"
	"strings"
	"time"
)

// Config is the fully validated, typed configuration for the API process.
type Config struct {
	AppEnv       string
	AppBaseURL   *url.URL
	LogLevel     slog.Level
	HTTPPort     int
	CORSOrigin   string
	CookieSecure bool

	Postgres Postgres
	Redis    Redis
	Auth     Auth
	SMTP     SMTP
	Limits   Limits
	Features Features
}

// Limits holds every numeric threshold spec 20.5 mandates come from config,
// never a magic number in code (spec 12.1).
type Limits struct {
	MaxUploadBytes      int64
	MaxAudioSeconds     int
	QueueMaxLength      int
	UserAnalysesPerHour int
	AudioTTLSeconds     int
}

// Features gates functionality that is off by default in production (spec 11.4).
type Features struct {
	YouTubeImport bool
	// YouTubePotProviderURL is the bgutil PO Token provider sidecar's base URL
	// (ADR-0037), passed to yt-dlp via --extractor-args whenever YouTubeImport
	// is enabled. Defaults to the compose service name/port so no deployment
	// needs to set it explicitly; only an operator running the sidecar
	// somewhere else needs to override it.
	YouTubePotProviderURL string
}

// Postgres holds connection settings for the primary database.
type Postgres struct {
	Host     string
	Port     int
	DB       string
	User     string
	Password string
}

// DSN builds a libpq connection string. Postgres is never reachable outside the
// compose network (spec 5.3), so TLS between api and postgres is not required.
func (p Postgres) DSN() string {
	return fmt.Sprintf("host=%s port=%d dbname=%s user=%s password=%s sslmode=disable",
		p.Host, p.Port, p.DB, p.User, p.Password)
}

// Redis holds connection settings for sessions, rate limiting and refresh tokens.
type Redis struct {
	Host     string
	Port     int
	Password string
	DB       int
}

// Addr returns the host:port address for the go-redis client.
func (r Redis) Addr() string {
	return fmt.Sprintf("%s:%d", r.Host, r.Port)
}

// Auth holds JWT signing and OAuth settings.
type Auth struct {
	JWTSecret          string
	AccessTokenTTL     time.Duration
	RefreshTokenTTL    time.Duration
	GoogleClientID     string
	GoogleClientSecret string
}

// GoogleRedirectURL is derived from AppBaseURL instead of a separate env var,
// so the callback origin can never drift from the public base URL (DRY).
func GoogleRedirectURL(appBaseURL *url.URL) string {
	u := *appBaseURL
	u.Path = strings.TrimRight(u.Path, "/") + "/api/v1/auth/google/callback"
	return u.String()
}

// SMTP holds outbound mail settings used to send verification codes.
type SMTP struct {
	Host     string
	Port     int
	User     string
	Password string
	From     string
}

// Addr returns the host:port address for the SMTP server.
func (s SMTP) Addr() string {
	return fmt.Sprintf("%s:%d", s.Host, s.Port)
}

// Load reads the environment, applies defaults, validates every field, and
// returns a single aggregated error describing every problem found.
func Load() (*Config, error) {
	var problems []string

	req := func(name string) string {
		v := os.Getenv(name)
		if v == "" {
			problems = append(problems, name+" is required")
		}
		return v
	}
	optInt := func(name string, def int) int {
		v := os.Getenv(name)
		if v == "" {
			return def
		}
		n, err := strconv.Atoi(v)
		if err != nil {
			problems = append(problems, name+" must be an integer: "+err.Error())
			return def
		}
		return n
	}
	optDuration := func(name string, def time.Duration) time.Duration {
		v := os.Getenv(name)
		if v == "" {
			return def
		}
		d, err := time.ParseDuration(v)
		if err != nil {
			problems = append(problems, name+" must be a duration (e.g. 15m): "+err.Error())
			return def
		}
		return d
	}
	optString := func(name, def string) string {
		if v := os.Getenv(name); v != "" {
			return v
		}
		return def
	}
	optBool := func(name string, def bool) bool {
		v := os.Getenv(name)
		if v == "" {
			return def
		}
		b, err := strconv.ParseBool(v)
		if err != nil {
			problems = append(problems, name+" must be a boolean: "+err.Error())
			return def
		}
		return b
	}
	posInt := func(name string, def int) int {
		n := optInt(name, def)
		if n <= 0 {
			problems = append(problems, name+" must be a positive integer")
		}
		return n
	}

	appEnv := optString("APP_ENV", "development")
	if appEnv != "development" && appEnv != "production" {
		problems = append(problems, "APP_ENV must be 'development' or 'production'")
	}

	rawBaseURL := req("APP_BASE_URL")
	baseURL, err := url.Parse(rawBaseURL)
	if rawBaseURL != "" && (err != nil || baseURL.Scheme == "" || baseURL.Host == "") {
		problems = append(problems, "APP_BASE_URL must be an absolute URL")
	}

	logLevel := slog.LevelInfo
	if v := optString("LOG_LEVEL", "info"); true {
		if err := logLevel.UnmarshalText([]byte(v)); err != nil {
			problems = append(problems, "LOG_LEVEL must be one of debug|info|warn|error")
		}
	}

	jwtSecret := req("JWT_SECRET")
	if jwtSecret != "" && len(jwtSecret) < 32 {
		problems = append(problems, "JWT_SECRET must be at least 32 characters")
	}

	cfg := &Config{
		AppEnv:       appEnv,
		AppBaseURL:   baseURL,
		LogLevel:     logLevel,
		HTTPPort:     optInt("API_HTTP_PORT", 8080),
		CORSOrigin:   req("CORS_ALLOWED_ORIGIN"),
		CookieSecure: appEnv == "production",

		Postgres: Postgres{
			Host:     optString("POSTGRES_HOST", "postgres"),
			Port:     optInt("POSTGRES_PORT", 5432),
			DB:       req("POSTGRES_DB"),
			User:     req("POSTGRES_USER"),
			Password: req("POSTGRES_PASSWORD"),
		},
		Redis: Redis{
			Host:     optString("REDIS_HOST", "redis"),
			Port:     optInt("REDIS_PORT", 6379),
			Password: req("REDIS_PASSWORD"),
			DB:       optInt("REDIS_DB", 0),
		},
		Auth: Auth{
			JWTSecret:          jwtSecret,
			AccessTokenTTL:     optDuration("ACCESS_TOKEN_TTL", 15*time.Minute),
			RefreshTokenTTL:    optDuration("REFRESH_TOKEN_TTL", 720*time.Hour),
			GoogleClientID:     req("GOOGLE_CLIENT_ID"),
			GoogleClientSecret: req("GOOGLE_CLIENT_SECRET"),
		},
		SMTP: SMTP{
			Host:     req("SMTP_HOST"),
			Port:     optInt("SMTP_PORT", 587),
			User:     optString("SMTP_USER", ""),
			Password: optString("SMTP_PASSWORD", ""),
			From:     req("SMTP_FROM"),
		},
		Limits: Limits{
			MaxUploadBytes:      int64(posInt("MAX_UPLOAD_MB", 15)) * 1024 * 1024,
			MaxAudioSeconds:     posInt("MAX_AUDIO_SECONDS", 360),
			QueueMaxLength:      posInt("QUEUE_MAX_LENGTH", 20),
			UserAnalysesPerHour: posInt("USER_ANALYSES_PER_HOUR", 10),
			AudioTTLSeconds:     posInt("AUDIO_TTL_SECONDS", 300),
		},
		Features: Features{
			YouTubeImport:         optBool("FEATURE_YOUTUBE_IMPORT", false),
			YouTubePotProviderURL: optString("YOUTUBE_POT_PROVIDER_URL", "http://pot-provider:4416"),
		},
	}

	if len(problems) > 0 {
		return nil, fmt.Errorf("invalid configuration:\n  - %s", strings.Join(problems, "\n  - "))
	}
	return cfg, nil
}
