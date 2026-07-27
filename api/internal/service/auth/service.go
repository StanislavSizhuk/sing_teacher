package auth

import (
	"crypto/sha256"
	"encoding/hex"
	"strings"
	"time"
)

// verificationCodeTTL is how long a 6-digit email verification code stays
// valid (FR-03).
const verificationCodeTTL = 24 * time.Hour

// maxVerificationAttempts is how many wrong codes are tolerated before the
// code is invalidated outright (spec 9.1).
const maxVerificationAttempts = 5

// Service implements account registration, email verification, login,
// session refresh, Google sign-in and account deletion.
type Service struct {
	users          UserRepository
	tokens         RefreshTokenStore
	mailer         Mailer
	hasher         PasswordHasher
	access         AccessTokenIssuer
	loginThrottle  LoginThrottle
	verifyThrottle VerificationThrottle
	google         GoogleVerifier
	clock          Clock
}

// NewService wires the auth service to its dependencies.
func NewService(
	users UserRepository,
	tokens RefreshTokenStore,
	mailer Mailer,
	hasher PasswordHasher,
	access AccessTokenIssuer,
	loginThrottle LoginThrottle,
	verifyThrottle VerificationThrottle,
	google GoogleVerifier,
	clock Clock,
) *Service {
	return &Service{
		users:          users,
		tokens:         tokens,
		mailer:         mailer,
		hasher:         hasher,
		access:         access,
		loginThrottle:  loginThrottle,
		verifyThrottle: verifyThrottle,
		google:         google,
		clock:          clock,
	}
}

// Session is the pair of tokens returned by every flow that logs a user in.
type Session struct {
	AccessToken  string
	RefreshToken string
}

func normalizeEmail(email string) string {
	return strings.ToLower(strings.TrimSpace(email))
}

// loginThrottleKey binds an (email, ip) pair to one opaque throttle bucket,
// so raw emails/IPs never appear as Redis key material.
func loginThrottleKey(email, ip string) string {
	sum := sha256.Sum256([]byte(normalizeEmail(email) + "|" + ip))
	return hex.EncodeToString(sum[:])
}
