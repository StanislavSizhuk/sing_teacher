// Package auth contains the account/session business logic: registration,
// email verification, login, refresh-token rotation, Google sign-in and
// account deletion. Every external dependency is declared here, as an
// interface, and implemented in internal/repository, internal/security,
// internal/mailer and internal/oauth.
package auth

import (
	"context"
	"time"

	"github.com/google/uuid"

	"ai-vocal-coach/api/internal/domain"
)

// UserRepository persists and looks up accounts.
type UserRepository interface {
	Create(ctx context.Context, u *domain.User) error
	GetByEmail(ctx context.Context, email string) (*domain.User, error)
	GetByID(ctx context.Context, id uuid.UUID) (*domain.User, error)
	GetByGoogleID(ctx context.Context, googleID string) (*domain.User, error)
	UpdateVerificationCode(ctx context.Context, userID uuid.UUID, codeHash string, expiresAt time.Time) error
	IncrementVerificationAttempts(ctx context.Context, userID uuid.UUID) (int, error)
	MarkVerified(ctx context.Context, userID uuid.UUID) error
	LinkGoogleID(ctx context.Context, userID uuid.UUID, googleID string) error
	HardDelete(ctx context.Context, userID uuid.UUID) error
	SoftDeleteExpiredUnverified(ctx context.Context) (int64, error)
}

// RefreshTokenStore issues, rotates and revokes refresh tokens.
type RefreshTokenStore interface {
	Issue(ctx context.Context, userID uuid.UUID) (token string, err error)
	Rotate(ctx context.Context, token string) (newToken string, userID uuid.UUID, err error)
	Revoke(ctx context.Context, token string) error
	RevokeAllForUser(ctx context.Context, userID uuid.UUID) error
}

// Mailer sends the transactional emails auth needs.
type Mailer interface {
	SendVerificationCode(ctx context.Context, to, code string) error
}

// PasswordHasher hashes and verifies both passwords and verification codes.
type PasswordHasher interface {
	Hash(password string) (string, error)
	Verify(password, hash string) (bool, error)
	DummyHash() string
}

// AccessTokenIssuer mints and validates short-lived access tokens.
type AccessTokenIssuer interface {
	Issue(userID uuid.UUID) (string, error)
	Parse(token string) (uuid.UUID, error)
}

// LoginThrottle protects login against brute force per (email, IP) key.
type LoginThrottle interface {
	Check(ctx context.Context, key string) (locked bool, retryAfter time.Duration, err error)
	RecordFailure(ctx context.Context, key string) error
	Reset(ctx context.Context, key string) error
}

// VerificationThrottle rate-limits verification-code resends.
type VerificationThrottle interface {
	// AllowResend reports whether a resend may proceed now. dailyLimitReached
	// distinguishes the once-per-day cap from the 60s cooldown so the caller
	// can surface the right error code.
	AllowResend(ctx context.Context, userID uuid.UUID) (allowed bool, retryAfter time.Duration, dailyLimitReached bool, err error)
}

// GoogleIdentity is the verified identity returned by Google after a
// successful OAuth exchange.
type GoogleIdentity struct {
	Subject       string
	Email         string
	EmailVerified bool
	Name          string
}

// GoogleVerifier drives the Google OAuth2/OIDC + PKCE flow.
type GoogleVerifier interface {
	// AuthCodeURL returns the URL to send the browser to, binding state and
	// the PKCE code challenge derived from codeVerifier.
	AuthCodeURL(ctx context.Context, state, codeVerifier string) (string, error)
	// Exchange trades an authorization code for a verified identity.
	Exchange(ctx context.Context, code, codeVerifier string) (GoogleIdentity, error)
}

// Clock is injected so time-dependent logic (expiry, lockouts) is testable.
type Clock interface {
	Now() time.Time
}

// RealClock is the production Clock.
type RealClock struct{}

// Now returns time.Now().
func (RealClock) Now() time.Time { return time.Now() }
