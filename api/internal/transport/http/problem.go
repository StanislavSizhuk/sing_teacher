package httptransport

import (
	"encoding/json"
	"errors"
	"log/slog"
	"math"
	"net/http"
	"strconv"

	"ai-vocal-coach/api/internal/domain"
)

// problemDetails is an RFC 9457 application/problem+json body (spec 8.1).
type problemDetails struct {
	Type      string `json:"type"`
	Title     string `json:"title"`
	Status    int    `json:"status"`
	Detail    string `json:"detail"`
	Code      string `json:"code"`
	RequestID string `json:"request_id"`
}

func writeProblem(w http.ResponseWriter, r *http.Request, status int, title, detail, code string) {
	w.Header().Set("Content-Type", "application/problem+json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(problemDetails{
		Type:      "about:blank",
		Title:     title,
		Status:    status,
		Detail:    detail,
		Code:      code,
		RequestID: requestIDFromContext(r.Context()),
	})
}

// writeServiceError classifies a service-layer error into an HTTP response.
// Unknown errors never leak their message to the client -- they are logged
// with full detail server-side and reported to the caller as a generic 500.
func writeServiceError(logger *slog.Logger, w http.ResponseWriter, r *http.Request, err error) {
	cause := err
	var throttled *domain.ThrottledError
	if errors.As(err, &throttled) {
		cause = throttled.Err
		seconds := int(math.Ceil(throttled.RetryAfter.Seconds()))
		if seconds < 1 {
			seconds = 1
		}
		w.Header().Set("Retry-After", strconv.Itoa(seconds))
	}

	status, title, detail, code := classify(cause)
	if status == http.StatusInternalServerError {
		logger.Error("unhandled service error", "error", err.Error(), "request_id", requestIDFromContext(r.Context()))
	}
	writeProblem(w, r, status, title, detail, code)
}

func classify(err error) (status int, title, detail, code string) {
	switch {
	case errors.Is(err, domain.ErrNotFound):
		return http.StatusNotFound, "Not Found", "The requested resource does not exist.", "NOT_FOUND"
	case errors.Is(err, domain.ErrEmailTaken):
		return http.StatusConflict, "Conflict", "This account is already linked elsewhere.", "EMAIL_TAKEN"
	case errors.Is(err, domain.ErrWeakPassword):
		return http.StatusBadRequest, "Bad Request", "Password must be at least 10 characters and not one of the most common passwords.", "WEAK_PASSWORD"
	case errors.Is(err, domain.ErrInvalidCredentials):
		return http.StatusUnauthorized, "Unauthorized", "Invalid email or password.", "INVALID_CREDENTIALS"
	case errors.Is(err, domain.ErrAccountLocked):
		return http.StatusTooManyRequests, "Too Many Requests", "Too many failed attempts. Try again later.", "ACCOUNT_LOCKED"
	case errors.Is(err, domain.ErrEmailNotVerified):
		return http.StatusForbidden, "Forbidden", "Please verify your email before logging in.", "EMAIL_NOT_VERIFIED"
	case errors.Is(err, domain.ErrVerificationCodeInvalid):
		return http.StatusBadRequest, "Bad Request", "Verification code is invalid.", "VERIFICATION_CODE_INVALID"
	case errors.Is(err, domain.ErrVerificationCodeExpired):
		return http.StatusBadRequest, "Bad Request", "Verification code has expired.", "VERIFICATION_CODE_EXPIRED"
	case errors.Is(err, domain.ErrTooManyVerificationAttempts):
		return http.StatusTooManyRequests, "Too Many Requests", "Too many incorrect attempts. Request a new code.", "VERIFICATION_ATTEMPTS_EXCEEDED"
	case errors.Is(err, domain.ErrVerificationCooldown):
		return http.StatusTooManyRequests, "Too Many Requests", "A code was already sent recently. Please wait.", "VERIFICATION_COOLDOWN"
	case errors.Is(err, domain.ErrVerificationDailyLimit):
		return http.StatusTooManyRequests, "Too Many Requests", "Daily verification resend limit reached.", "VERIFICATION_DAILY_LIMIT"
	case errors.Is(err, domain.ErrRefreshTokenReused):
		return http.StatusUnauthorized, "Unauthorized", "Session invalidated for your protection. Please log in again.", "REFRESH_TOKEN_REUSED"
	case errors.Is(err, domain.ErrRefreshTokenInvalid):
		return http.StatusUnauthorized, "Unauthorized", "Session expired. Please log in again.", "REFRESH_TOKEN_INVALID"
	case errors.Is(err, domain.ErrGoogleEmailNotVerified):
		return http.StatusForbidden, "Forbidden", "Your Google account email is not verified.", "GOOGLE_EMAIL_NOT_VERIFIED"
	case errors.Is(err, domain.ErrOAuthState):
		return http.StatusBadRequest, "Bad Request", "OAuth flow could not be verified. Please try again.", "OAUTH_STATE_MISMATCH"
	case errors.Is(err, domain.ErrInvalidAccessToken):
		return http.StatusUnauthorized, "Unauthorized", "Missing or invalid access token.", "UNAUTHORIZED"
	default:
		return http.StatusInternalServerError, "Internal Server Error", "Something went wrong. Please try again.", "INTERNAL"
	}
}
