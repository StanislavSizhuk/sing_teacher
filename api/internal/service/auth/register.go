package auth

import (
	"context"
	"crypto/rand"
	"errors"
	"fmt"
	"math/big"

	"github.com/google/uuid"

	"ai-vocal-coach/api/internal/domain"
)

// Register creates a new email+password account and emails it a verification
// code. It never reveals whether the email was already registered: on any
// "already exists" branch it still does argon2id-equivalent work and returns
// nil, exactly as on success (spec 9.1).
func (s *Service) Register(ctx context.Context, email, password, displayName string) error {
	email = normalizeEmail(email)
	if err := validatePasswordPolicy(password); err != nil {
		return err
	}

	// Hash unconditionally, before we know whether the account already
	// exists, so the two branches below cost the same wall-clock time.
	passwordHash, err := s.hasher.Hash(password)
	if err != nil {
		return fmt.Errorf("hash password: %w", err)
	}
	code, codeHash, err := s.newVerificationCode()
	if err != nil {
		return err
	}
	expiresAt := s.clock.Now().Add(verificationCodeTTL)

	existing, err := s.users.GetByEmail(ctx, email)
	if err != nil && !errors.Is(err, domain.ErrNotFound) {
		return fmt.Errorf("look up existing user: %w", err)
	}

	if existing != nil {
		if !existing.EmailVerified {
			// Best-effort: refresh their code so a legitimate owner who
			// lost the first email can still get in via resend/register.
			if allowed, _, _, err := s.verifyThrottle.AllowResend(ctx, existing.ID); err == nil && allowed {
				if err := s.users.UpdateVerificationCode(ctx, existing.ID, codeHash, expiresAt); err == nil {
					_ = s.mailer.SendVerificationCode(ctx, email, code)
				}
			}
		}
		return nil
	}

	user := &domain.User{
		ID:                    uuid.New(),
		Email:                 email,
		PasswordHash:          &passwordHash,
		DisplayName:           displayName,
		EmailVerified:         false,
		VerificationCodeHash:  &codeHash,
		VerificationExpiresAt: &expiresAt,
	}
	if err := s.users.Create(ctx, user); err != nil {
		if errors.Is(err, domain.ErrEmailTaken) {
			return nil // lost a race with a concurrent registration; still don't leak
		}
		return fmt.Errorf("create user: %w", err)
	}

	if err := s.mailer.SendVerificationCode(ctx, email, code); err != nil {
		return fmt.Errorf("send verification email: %w", err)
	}
	return nil
}

// newVerificationCode generates a uniformly random 6-digit code and its
// argon2id hash. crypto/rand is used deliberately -- this code gates account
// takeover, so it must not be predictable like math/rand output would be.
func (s *Service) newVerificationCode() (code string, hash string, err error) {
	n, err := rand.Int(rand.Reader, big.NewInt(1_000_000))
	if err != nil {
		return "", "", fmt.Errorf("generate verification code: %w", err)
	}
	code = fmt.Sprintf("%06d", n.Int64())
	hash, err = s.hasher.Hash(code)
	if err != nil {
		return "", "", fmt.Errorf("hash verification code: %w", err)
	}
	return code, hash, nil
}
