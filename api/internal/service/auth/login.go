package auth

import (
	"context"
	"errors"
	"fmt"

	"github.com/google/uuid"

	"ai-vocal-coach/api/internal/domain"
)

// Login authenticates by email+password and, on success, issues a new
// session. It never reveals whether the email exists: an unknown email and a
// wrong password return the identical domain.ErrInvalidCredentials, checked
// against a precomputed dummy hash so the timing matches too (spec 9.1).
// Once the password has actually been proven correct, "not verified yet" is
// safe to disclose -- only someone who already knows the password reaches it.
func (s *Service) Login(ctx context.Context, email, password, ip string) (*Session, error) {
	email = normalizeEmail(email)
	throttleKey := loginThrottleKey(email, ip)

	locked, retryAfter, err := s.loginThrottle.Check(ctx, throttleKey)
	if err != nil {
		return nil, fmt.Errorf("check login throttle: %w", err)
	}
	if locked {
		return nil, &domain.ThrottledError{Err: domain.ErrAccountLocked, RetryAfter: retryAfter}
	}

	user, err := s.users.GetByEmail(ctx, email)
	if err != nil && !errors.Is(err, domain.ErrNotFound) {
		return nil, fmt.Errorf("look up user: %w", err)
	}

	realAccount := user != nil && user.PasswordHash != nil
	hashToCheck := s.hasher.DummyHash()
	if realAccount {
		hashToCheck = *user.PasswordHash
	}

	ok, err := s.hasher.Verify(password, hashToCheck)
	if err != nil {
		return nil, fmt.Errorf("verify password: %w", err)
	}

	if !realAccount || !ok {
		if err := s.loginThrottle.RecordFailure(ctx, throttleKey); err != nil {
			return nil, fmt.Errorf("record login failure: %w", err)
		}
		return nil, domain.ErrInvalidCredentials
	}

	if !user.EmailVerified {
		return nil, domain.ErrEmailNotVerified
	}

	if err := s.loginThrottle.Reset(ctx, throttleKey); err != nil {
		return nil, fmt.Errorf("reset login throttle: %w", err)
	}
	return s.issueSession(ctx, user.ID)
}

func (s *Service) issueSession(ctx context.Context, userID uuid.UUID) (*Session, error) {
	accessToken, err := s.access.Issue(userID)
	if err != nil {
		return nil, fmt.Errorf("issue access token: %w", err)
	}
	refreshToken, err := s.tokens.Issue(ctx, userID)
	if err != nil {
		return nil, fmt.Errorf("issue refresh token: %w", err)
	}
	return &Session{AccessToken: accessToken, RefreshToken: refreshToken}, nil
}
