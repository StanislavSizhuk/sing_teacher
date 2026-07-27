package config_test

import (
	"strings"
	"testing"
	"time"

	"github.com/stretchr/testify/require"

	"ai-vocal-coach/api/internal/config"
)

// setRequiredEnv sets every variable Load() requires to a valid value, so a
// single test can override just the one it cares about.
func setRequiredEnv(t *testing.T) {
	t.Helper()
	vars := map[string]string{
		"APP_BASE_URL":         "https://example.com",
		"JWT_SECRET":           strings.Repeat("s", 32),
		"CORS_ALLOWED_ORIGIN":  "https://example.com",
		"POSTGRES_DB":          "vocalcoach",
		"POSTGRES_USER":        "vocalcoach",
		"POSTGRES_PASSWORD":    "postgres-password",
		"REDIS_PASSWORD":       "redis-password",
		"GOOGLE_CLIENT_ID":     "client-id",
		"GOOGLE_CLIENT_SECRET": "client-secret",
		"SMTP_HOST":            "smtp.example.com",
		"SMTP_FROM":            "noreply@example.com",
	}
	for k, v := range vars {
		t.Setenv(k, v)
	}
}

func TestLoad_ValidConfig_AppliesDefaults(t *testing.T) {
	setRequiredEnv(t)

	cfg, err := config.Load()
	require.NoError(t, err)
	require.Equal(t, "development", cfg.AppEnv)
	require.Equal(t, 8080, cfg.HTTPPort)
	require.Equal(t, 15*time.Minute, cfg.Auth.AccessTokenTTL)
	require.Equal(t, 720*time.Hour, cfg.Auth.RefreshTokenTTL)
	require.Equal(t, "postgres", cfg.Postgres.Host)
	require.Equal(t, 5432, cfg.Postgres.Port)
	require.Equal(t, "redis", cfg.Redis.Host)
	require.Equal(t, 6379, cfg.Redis.Port)
	require.False(t, cfg.CookieSecure, "development must not require Secure cookies")
	require.Equal(t, int64(15*1024*1024), cfg.Limits.MaxUploadBytes)
	require.Equal(t, 360, cfg.Limits.MaxAudioSeconds)
	require.Equal(t, 20, cfg.Limits.QueueMaxLength)
	require.Equal(t, 10, cfg.Limits.UserAnalysesPerHour)
	require.Equal(t, 300, cfg.Limits.AudioTTLSeconds)
	require.False(t, cfg.Features.YouTubeImport, "youtube import must default to off (spec 11.4)")
}

func TestLoad_FeatureYouTubeImport_ParsesTrue(t *testing.T) {
	setRequiredEnv(t)
	t.Setenv("FEATURE_YOUTUBE_IMPORT", "true")

	cfg, err := config.Load()
	require.NoError(t, err)
	require.True(t, cfg.Features.YouTubeImport)
}

func TestLoad_InvalidFeatureFlag_Rejected(t *testing.T) {
	setRequiredEnv(t)
	t.Setenv("FEATURE_YOUTUBE_IMPORT", "maybe")

	_, err := config.Load()
	require.ErrorContains(t, err, "FEATURE_YOUTUBE_IMPORT")
}

func TestLoad_NonPositiveLimit_Rejected(t *testing.T) {
	setRequiredEnv(t)
	t.Setenv("QUEUE_MAX_LENGTH", "0")

	_, err := config.Load()
	require.ErrorContains(t, err, "QUEUE_MAX_LENGTH")
}

func TestLoad_ProductionEnv_RequiresSecureCookies(t *testing.T) {
	setRequiredEnv(t)
	t.Setenv("APP_ENV", "production")

	cfg, err := config.Load()
	require.NoError(t, err)
	require.True(t, cfg.CookieSecure)
}

func TestLoad_MissingRequiredVars_ReportsAllOfThem(t *testing.T) {
	// Deliberately do not call setRequiredEnv: every required var is missing.
	_, err := config.Load()
	require.Error(t, err)
	for _, want := range []string{"APP_BASE_URL", "JWT_SECRET", "CORS_ALLOWED_ORIGIN", "POSTGRES_DB", "REDIS_PASSWORD"} {
		require.Contains(t, err.Error(), want)
	}
}

func TestLoad_InvalidAppEnv_Rejected(t *testing.T) {
	setRequiredEnv(t)
	t.Setenv("APP_ENV", "staging")

	_, err := config.Load()
	require.ErrorContains(t, err, "APP_ENV")
}

func TestLoad_ShortJWTSecret_Rejected(t *testing.T) {
	setRequiredEnv(t)
	t.Setenv("JWT_SECRET", "too-short")

	_, err := config.Load()
	require.ErrorContains(t, err, "JWT_SECRET")
}

func TestLoad_InvalidBaseURL_Rejected(t *testing.T) {
	setRequiredEnv(t)
	t.Setenv("APP_BASE_URL", "not-a-url")

	_, err := config.Load()
	require.ErrorContains(t, err, "APP_BASE_URL")
}

func TestLoad_InvalidLogLevel_Rejected(t *testing.T) {
	setRequiredEnv(t)
	t.Setenv("LOG_LEVEL", "verbose")

	_, err := config.Load()
	require.ErrorContains(t, err, "LOG_LEVEL")
}

func TestGoogleRedirectURL_DerivedFromBaseURL(t *testing.T) {
	setRequiredEnv(t)
	cfg, err := config.Load()
	require.NoError(t, err)
	require.Equal(t, "https://example.com/api/v1/auth/google/callback", config.GoogleRedirectURL(cfg.AppBaseURL))
}
