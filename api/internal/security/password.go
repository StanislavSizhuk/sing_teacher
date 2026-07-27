// Package security implements the auth service's password-hashing and
// access-token interfaces.
package security

import (
	"fmt"

	"github.com/alexedwards/argon2id"
)

// argonParams follows OWASP's current minimum recommendation for argon2id
// (m=64MB, t=3, p=2..4) rather than the library default of t=1, which is
// tuned for very high-throughput services, not a solo-user login endpoint.
var argonParams = &argon2id.Params{
	Memory:      64 * 1024,
	Iterations:  3,
	Parallelism: 2,
	SaltLength:  16,
	KeyLength:   32,
}

// PasswordHasher hashes and verifies passwords (and, reused for its strong,
// constant-time comparison, 6-digit verification codes) with argon2id.
type PasswordHasher struct {
	// dummyHash is a precomputed hash checked when no real account exists, so
	// "unknown email" and "wrong password" cost the same wall-clock time and
	// cannot be told apart by timing (spec 9.1).
	dummyHash string
}

// NewPasswordHasher precomputes the dummy hash used for timing parity.
func NewPasswordHasher() (*PasswordHasher, error) {
	dummy, err := argon2id.CreateHash("no-such-account-placeholder", argonParams)
	if err != nil {
		return nil, fmt.Errorf("precompute dummy hash: %w", err)
	}
	return &PasswordHasher{dummyHash: dummy}, nil
}

// Hash returns the argon2id encoded hash of password.
func (h *PasswordHasher) Hash(password string) (string, error) {
	hash, err := argon2id.CreateHash(password, argonParams)
	if err != nil {
		return "", fmt.Errorf("hash password: %w", err)
	}
	return hash, nil
}

// Verify reports whether password matches hash, in constant time.
func (h *PasswordHasher) Verify(password, hash string) (bool, error) {
	match, err := argon2id.ComparePasswordAndHash(password, hash)
	if err != nil {
		return false, fmt.Errorf("verify password: %w", err)
	}
	return match, nil
}

// DummyHash returns the precomputed placeholder hash for timing-parity checks.
func (h *PasswordHasher) DummyHash() string {
	return h.dummyHash
}
