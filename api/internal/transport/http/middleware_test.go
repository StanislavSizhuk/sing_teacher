package httptransport

import (
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/google/uuid"
	"github.com/stretchr/testify/require"

	"ai-vocal-coach/api/internal/domain"
)

func silentLogger() *slog.Logger {
	return slog.New(slog.NewTextHandler(io.Discard, nil))
}

// fakeTokenParser lets each test case drive AccessTokenParser without a real
// JWT issuer, so authMiddleware is exercised in isolation from auth service.
type fakeTokenParser struct {
	userID uuid.UUID
	err    error
}

func (f fakeTokenParser) Parse(string) (uuid.UUID, error) {
	return f.userID, f.err
}

func TestAuthMiddleware(t *testing.T) {
	validUserID := uuid.New()

	tests := []struct {
		name       string
		authHeader string
		parser     AccessTokenParser
		wantStatus int
		wantCode   string
	}{
		{
			name:       "missing Authorization header",
			parser:     fakeTokenParser{userID: validUserID},
			wantStatus: http.StatusUnauthorized,
			wantCode:   "UNAUTHORIZED",
		},
		{
			name:       "header without Bearer prefix",
			authHeader: "Basic dXNlcjpwYXNz",
			parser:     fakeTokenParser{userID: validUserID},
			wantStatus: http.StatusUnauthorized,
			wantCode:   "UNAUTHORIZED",
		},
		{
			name:       "parser rejects the token",
			authHeader: "Bearer bad-token",
			parser:     fakeTokenParser{err: domain.ErrInvalidAccessToken},
			wantStatus: http.StatusUnauthorized,
			wantCode:   "UNAUTHORIZED",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			nextCalled := false
			next := http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
				nextCalled = true
				w.WriteHeader(http.StatusOK)
			})

			handler := authMiddleware(silentLogger(), tt.parser)(next)

			req := httptest.NewRequest(http.MethodGet, "/api/v1/me", nil)
			if tt.authHeader != "" {
				req.Header.Set("Authorization", tt.authHeader)
			}
			rec := httptest.NewRecorder()
			handler.ServeHTTP(rec, req)

			require.False(t, nextCalled, "rejected requests must never reach the next handler")
			require.Equal(t, tt.wantStatus, rec.Code)

			var problem problemDetails
			require.NoError(t, json.NewDecoder(rec.Body).Decode(&problem))
			require.Equal(t, tt.wantCode, problem.Code)
		})
	}
}

func TestAuthMiddleware_ValidToken_PutsUserIDInContextAndCallsNext(t *testing.T) {
	validUserID := uuid.New()
	var gotUserID uuid.UUID
	var gotOK bool
	next := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotUserID, gotOK = userIDFromContext(r.Context())
		w.WriteHeader(http.StatusOK)
	})

	handler := authMiddleware(silentLogger(), fakeTokenParser{userID: validUserID})(next)

	req := httptest.NewRequest(http.MethodGet, "/api/v1/me", nil)
	req.Header.Set("Authorization", "Bearer good-token")
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	require.Equal(t, http.StatusOK, rec.Code)
	require.True(t, gotOK, "next handler must see a user id in context")
	require.Equal(t, validUserID, gotUserID)
}
