package httptransport

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/stretchr/testify/require"

	"ai-vocal-coach/api/internal/domain"
)

func TestClassify_MapsEverySentinelError(t *testing.T) {
	tests := []struct {
		name       string
		err        error
		wantStatus int
		wantCode   string
	}{
		{"not found", domain.ErrNotFound, http.StatusNotFound, "NOT_FOUND"},
		{"email taken", domain.ErrEmailTaken, http.StatusConflict, "EMAIL_TAKEN"},
		{"weak password", domain.ErrWeakPassword, http.StatusBadRequest, "WEAK_PASSWORD"},
		{"invalid credentials", domain.ErrInvalidCredentials, http.StatusUnauthorized, "INVALID_CREDENTIALS"},
		{"account locked", domain.ErrAccountLocked, http.StatusTooManyRequests, "ACCOUNT_LOCKED"},
		{"email not verified", domain.ErrEmailNotVerified, http.StatusForbidden, "EMAIL_NOT_VERIFIED"},
		{"verification code invalid", domain.ErrVerificationCodeInvalid, http.StatusBadRequest, "VERIFICATION_CODE_INVALID"},
		{"verification code expired", domain.ErrVerificationCodeExpired, http.StatusBadRequest, "VERIFICATION_CODE_EXPIRED"},
		{"too many verification attempts", domain.ErrTooManyVerificationAttempts, http.StatusTooManyRequests, "VERIFICATION_ATTEMPTS_EXCEEDED"},
		{"verification cooldown", domain.ErrVerificationCooldown, http.StatusTooManyRequests, "VERIFICATION_COOLDOWN"},
		{"verification daily limit", domain.ErrVerificationDailyLimit, http.StatusTooManyRequests, "VERIFICATION_DAILY_LIMIT"},
		{"refresh token reused", domain.ErrRefreshTokenReused, http.StatusUnauthorized, "REFRESH_TOKEN_REUSED"},
		{"refresh token invalid", domain.ErrRefreshTokenInvalid, http.StatusUnauthorized, "REFRESH_TOKEN_INVALID"},
		{"google email not verified", domain.ErrGoogleEmailNotVerified, http.StatusForbidden, "GOOGLE_EMAIL_NOT_VERIFIED"},
		{"oauth state mismatch", domain.ErrOAuthState, http.StatusBadRequest, "OAUTH_STATE_MISMATCH"},
		{"invalid access token", domain.ErrInvalidAccessToken, http.StatusUnauthorized, "UNAUTHORIZED"},
		{"unsupported audio format", domain.ErrUnsupportedAudioFormat, http.StatusBadRequest, "UNSUPPORTED_AUDIO_FORMAT"},
		{"audio too large", domain.ErrAudioTooLarge, http.StatusRequestEntityTooLarge, "AUDIO_TOO_LARGE"},
		{"audio too long", domain.ErrAudioTooLong, http.StatusBadRequest, "AUDIO_TOO_LONG"},
		{"youtube import disabled", domain.ErrYouTubeImportDisabled, http.StatusForbidden, "YOUTUBE_IMPORT_DISABLED"},
		{"invalid youtube url", domain.ErrInvalidYouTubeURL, http.StatusBadRequest, "INVALID_YOUTUBE_URL"},
		{"youtube video too long", domain.ErrYouTubeVideoTooLong, http.StatusBadRequest, "YOUTUBE_VIDEO_TOO_LONG"},
		{"queue full", domain.ErrQueueFull, http.StatusTooManyRequests, "QUEUE_FULL"},
		{"analysis rate limited", domain.ErrAnalysisRateLimited, http.StatusTooManyRequests, "ANALYSIS_RATE_LIMITED"},
		{"analysis not queued", domain.ErrAnalysisNotQueued, http.StatusConflict, "ANALYSIS_NOT_QUEUED"},
		{"analysis not failed", domain.ErrAnalysisNotFailed, http.StatusConflict, "ANALYSIS_NOT_FAILED"},
		{"unmapped error falls back to a generic 500", errors.New("some unmapped failure"), http.StatusInternalServerError, "INTERNAL"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			status, _, _, code := classify(tt.err)
			require.Equal(t, tt.wantStatus, status)
			require.Equal(t, tt.wantCode, code)
		})
	}
}

func TestClassify_UnwrapsWrappedSentinelErrors(t *testing.T) {
	wrapped := fmt.Errorf("repository lookup: %w", domain.ErrNotFound)

	status, _, _, code := classify(wrapped)

	require.Equal(t, http.StatusNotFound, status)
	require.Equal(t, "NOT_FOUND", code)
}

func TestWriteServiceError_ThrottledError_SetsRetryAfterHeader(t *testing.T) {
	err := &domain.ThrottledError{Err: domain.ErrAnalysisRateLimited, RetryAfter: 2500 * time.Millisecond}
	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodPost, "/api/v1/analyses", nil)

	writeServiceError(silentLogger(), rec, req, err)

	require.Equal(t, http.StatusTooManyRequests, rec.Code)
	require.Equal(t, "3", rec.Header().Get("Retry-After"), "RetryAfter must round up to whole seconds")

	var problem problemDetails
	require.NoError(t, json.NewDecoder(rec.Body).Decode(&problem))
	require.Equal(t, "ANALYSIS_RATE_LIMITED", problem.Code)
}

// writeServiceError must never leak an unmapped error's message to the
// client, but the full detail must still reach server-side logs so it can
// be debugged (spec 11.5, and the doc comment on writeServiceError itself).
func TestWriteServiceError_UnknownError_DoesNotLeakDetailButStillLogsIt(t *testing.T) {
	var logBuf bytes.Buffer
	logger := slog.New(slog.NewTextHandler(&logBuf, nil))
	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/api/v1/me", nil)

	writeServiceError(logger, rec, req, errors.New("pq: connection refused to internal-db-host:5432"))

	require.Equal(t, http.StatusInternalServerError, rec.Code)

	var problem problemDetails
	require.NoError(t, json.NewDecoder(rec.Body).Decode(&problem))
	require.Equal(t, "INTERNAL", problem.Code)
	require.NotContains(t, problem.Detail, "internal-db-host")

	require.Contains(t, logBuf.String(), "internal-db-host", "the raw error must still reach server-side logs")
}

func TestWriteProblem_SetsContentTypeAndEchoesRequestID(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/v1/me", nil)
	req = req.WithContext(withRequestID(req.Context(), "req-123"))
	rec := httptest.NewRecorder()

	writeProblem(rec, req, http.StatusTeapot, "I'm a teapot", "detail text", "TEAPOT")

	require.Equal(t, "application/problem+json", rec.Header().Get("Content-Type"))

	var problem problemDetails
	require.NoError(t, json.NewDecoder(rec.Body).Decode(&problem))
	require.Equal(t, http.StatusTeapot, problem.Status)
	require.Equal(t, "TEAPOT", problem.Code)
	require.Equal(t, "detail text", problem.Detail)
	require.Equal(t, "req-123", problem.RequestID)
}
