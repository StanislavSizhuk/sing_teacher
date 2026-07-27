package auth

import (
	"context"
	"errors"
	"fmt"

	"ai-vocal-coach/api/internal/domain"
)

// VerifyEmail checks a 6-digit code against the account's stored hash and, on
// success, marks the account verified. Verifying an already-verified account
// is idempotent.
func (s *Service) VerifyEmail(ctx context.Context, email, code string) error {
	email = normalizeEmail(email)
	user, err := s.users.GetByEmail(ctx, email)
	if err != nil {
		if errors.Is(err, domain.ErrNotFound) {
			return domain.ErrVerificationCodeInvalid
		}
		return fmt.Errorf("look up user: %w", err)
	}
	if user.EmailVerified {
		return nil
	}
	if user.VerificationExpiresAt == nil || s.clock.Now().After(*user.VerificationExpiresAt) {
		return domain.ErrVerificationCodeExpired
	}
	if user.VerificationAttempts >= maxVerificationAttempts {
		return domain.ErrTooManyVerificationAttempts
	}
	if user.VerificationCodeHash == nil {
		return domain.ErrVerificationCodeInvalid
	}

	ok, err := s.hasher.Verify(code, *user.VerificationCodeHash)
	if err != nil {
		return fmt.Errorf("verify code: %w", err)
	}
	if !ok {
		if _, err := s.users.IncrementVerificationAttempts(ctx, user.ID); err != nil {
			return fmt.Errorf("record failed attempt: %w", err)
		}
		return domain.ErrVerificationCodeInvalid
	}

	if err := s.users.MarkVerified(ctx, user.ID); err != nil {
		return fmt.Errorf("mark verified: %w", err)
	}
	return nil
}

// ResendVerification issues a fresh code, subject to the resend throttle
// (FR-04). Unlike Register/Login, this deliberately does not hide whether the
// account exists: the client-visible cooldown countdown (FR-04) requires it,
// and the spec's anti-enumeration requirement (9.1) only names login/register.
func (s *Service) ResendVerification(ctx context.Context, email string) error {
	email = normalizeEmail(email)
	user, err := s.users.GetByEmail(ctx, email)
	if err != nil {
		if errors.Is(err, domain.ErrNotFound) {
			return nil
		}
		return fmt.Errorf("look up user: %w", err)
	}
	if user.EmailVerified {
		return nil
	}

	allowed, retryAfter, dailyLimitReached, err := s.verifyThrottle.AllowResend(ctx, user.ID)
	if err != nil {
		return fmt.Errorf("check resend throttle: %w", err)
	}
	if !allowed {
		reason := domain.ErrVerificationCooldown
		if dailyLimitReached {
			reason = domain.ErrVerificationDailyLimit
		}
		return &domain.ThrottledError{Err: reason, RetryAfter: retryAfter}
	}

	code, codeHash, err := s.newVerificationCode()
	if err != nil {
		return err
	}
	expiresAt := s.clock.Now().Add(verificationCodeTTL)
	if err := s.users.UpdateVerificationCode(ctx, user.ID, codeHash, expiresAt); err != nil {
		return fmt.Errorf("update verification code: %w", err)
	}
	if err := s.mailer.SendVerificationCode(ctx, email, code); err != nil {
		return fmt.Errorf("send verification email: %w", err)
	}
	return nil
}
