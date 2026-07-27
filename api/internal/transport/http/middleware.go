package httptransport

import (
	"log/slog"
	"net/http"
	"strings"
	"time"

	"github.com/google/uuid"

	"ai-vocal-coach/api/internal/domain"
)

// AccessTokenParser is the narrow slice of auth.AccessTokenIssuer the HTTP
// layer needs: it only ever validates incoming tokens, never mints them.
type AccessTokenParser interface {
	Parse(token string) (uuid.UUID, error)
}

// statusRecorder captures the status code written by the handler so the
// logging middleware can report it after the fact.
type statusRecorder struct {
	http.ResponseWriter
	status int
}

func (r *statusRecorder) WriteHeader(status int) {
	r.status = status
	r.ResponseWriter.WriteHeader(status)
}

// requestIDMiddleware assigns every request a fresh id (never trusting one
// from the client) and echoes it back in X-Request-Id (spec 8.1).
func requestIDMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		id := uuid.NewString()
		w.Header().Set("X-Request-Id", id)
		next.ServeHTTP(w, r.WithContext(withRequestID(r.Context(), id)))
	})
}

// recoverMiddleware turns a panic into a 500 instead of crashing the process
// (spec 12.2: no panics on the request path).
func recoverMiddleware(logger *slog.Logger) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			defer func() {
				if rec := recover(); rec != nil {
					logger.Error("panic recovered", "panic", rec, "request_id", requestIDFromContext(r.Context()))
					writeProblem(w, r, http.StatusInternalServerError, "Internal Server Error", "Something went wrong. Please try again.", "INTERNAL")
				}
			}()
			next.ServeHTTP(w, r)
		})
	}
}

// loggingMiddleware emits one structured JSON line per request (spec 17.2).
// user_id is logged as a uuid, never an email, and no request/response body
// is ever logged.
func loggingMiddleware(logger *slog.Logger) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			start := time.Now()
			rec := &statusRecorder{ResponseWriter: w, status: http.StatusOK}
			next.ServeHTTP(rec, r)

			attrs := []any{
				"method", r.Method,
				"path", r.URL.Path,
				"status", rec.status,
				"duration_ms", time.Since(start).Milliseconds(),
				"request_id", requestIDFromContext(r.Context()),
			}
			if userID, ok := userIDFromContext(r.Context()); ok {
				attrs = append(attrs, "user_id", userID.String())
			}
			logger.Info("http_request", attrs...)
		})
	}
}

// corsMiddleware allows only the configured frontend origin, with
// credentials, per spec 11.2.
func corsMiddleware(allowedOrigin string) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if origin := r.Header.Get("Origin"); origin != "" && origin == allowedOrigin {
				w.Header().Set("Access-Control-Allow-Origin", origin)
				w.Header().Set("Access-Control-Allow-Credentials", "true")
				w.Header().Set("Vary", "Origin")
			}
			if r.Method == http.MethodOptions {
				w.Header().Set("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
				w.Header().Set("Access-Control-Allow-Headers", "Content-Type, Authorization")
				w.WriteHeader(http.StatusNoContent)
				return
			}
			next.ServeHTTP(w, r)
		})
	}
}

// authMiddleware requires a valid Bearer access token and puts the caller's
// user id in the request context.
func authMiddleware(logger *slog.Logger, tokens AccessTokenParser) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			header := r.Header.Get("Authorization")
			const prefix = "Bearer "
			if !strings.HasPrefix(header, prefix) {
				writeServiceError(logger, w, r, domain.ErrInvalidAccessToken)
				return
			}
			userID, err := tokens.Parse(strings.TrimPrefix(header, prefix))
			if err != nil {
				writeServiceError(logger, w, r, err)
				return
			}
			next.ServeHTTP(w, r.WithContext(withUserID(r.Context(), userID)))
		})
	}
}
