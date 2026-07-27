package auth_test

import (
	"context"
	"testing"
	"time"

	"github.com/stretchr/testify/require"

	"ai-vocal-coach/api/internal/domain"
	"ai-vocal-coach/api/internal/service/auth"
)

func registerAndGet(t *testing.T, svc *auth.Service, d *testDeps, email string) (*domain.User, string) {
	t.Helper()
	ctx := context.Background()
	require.NoError(t, svc.Register(ctx, email, "correct-horse-battery", "Test User"))
	user, err := d.users.GetByEmail(ctx, email)
	require.NoError(t, err)
	require.Len(t, d.mailer.sent, 1)
	return user, d.mailer.sent[0].Code
}

func TestVerifyEmail_CorrectCode_MarksVerified(t *testing.T) {
	svc, d := newTestService()
	_, code := registerAndGet(t, svc, d, "verify@example.com")

	err := svc.VerifyEmail(context.Background(), "verify@example.com", code)
	require.NoError(t, err)

	user, err := d.users.GetByEmail(context.Background(), "verify@example.com")
	require.NoError(t, err)
	require.True(t, user.EmailVerified)
	require.Nil(t, user.VerificationCodeHash)
}

func TestVerifyEmail_AlreadyVerified_Idempotent(t *testing.T) {
	svc, d := newTestService()
	_, code := registerAndGet(t, svc, d, "verify@example.com")
	require.NoError(t, svc.VerifyEmail(context.Background(), "verify@example.com", code))

	err := svc.VerifyEmail(context.Background(), "verify@example.com", "000000")
	require.NoError(t, err, "verifying twice must not error even with a wrong second code")
}

func TestVerifyEmail_WrongCode_IncrementsAttempts(t *testing.T) {
	svc, d := newTestService()
	user, _ := registerAndGet(t, svc, d, "verify@example.com")

	err := svc.VerifyEmail(context.Background(), "verify@example.com", "000000")
	require.ErrorIs(t, err, domain.ErrVerificationCodeInvalid)

	got, err := d.users.GetByID(context.Background(), user.ID)
	require.NoError(t, err)
	require.Equal(t, 1, got.VerificationAttempts)
}

func TestVerifyEmail_TooManyAttempts_CodeDead(t *testing.T) {
	svc, d := newTestService()
	user, code := registerAndGet(t, svc, d, "verify@example.com")
	for i := 0; i < 5; i++ {
		_, err := d.users.IncrementVerificationAttempts(context.Background(), user.ID)
		require.NoError(t, err)
	}

	err := svc.VerifyEmail(context.Background(), "verify@example.com", code)
	require.ErrorIs(t, err, domain.ErrTooManyVerificationAttempts, "even the correct code must now be rejected")
}

func TestVerifyEmail_Expired(t *testing.T) {
	svc, d := newTestService()
	_, code := registerAndGet(t, svc, d, "verify@example.com")
	d.clock.now = d.clock.now.Add(25 * time.Hour)

	err := svc.VerifyEmail(context.Background(), "verify@example.com", code)
	require.ErrorIs(t, err, domain.ErrVerificationCodeExpired)
}

func TestVerifyEmail_UnknownEmail_GenericInvalid(t *testing.T) {
	svc, _ := newTestService()
	err := svc.VerifyEmail(context.Background(), "ghost@example.com", "123456")
	require.ErrorIs(t, err, domain.ErrVerificationCodeInvalid, "must not leak that the account does not exist")
}

func TestResendVerification_UnknownEmail_NoOp(t *testing.T) {
	svc, d := newTestService()
	err := svc.ResendVerification(context.Background(), "ghost@example.com")
	require.NoError(t, err)
	require.Empty(t, d.mailer.sent)
	require.Zero(t, d.verifyThrottle.calls, "should not even consult the throttle for a nonexistent account")
}

func TestResendVerification_AlreadyVerified_NoOp(t *testing.T) {
	svc, d := newTestService()
	_, code := registerAndGet(t, svc, d, "verify@example.com")
	require.NoError(t, svc.VerifyEmail(context.Background(), "verify@example.com", code))

	err := svc.ResendVerification(context.Background(), "verify@example.com")
	require.NoError(t, err)
	require.Len(t, d.mailer.sent, 1, "no new email for an already-verified account")
}

func TestResendVerification_Throttled(t *testing.T) {
	svc, d := newTestService()
	registerAndGet(t, svc, d, "verify@example.com")
	d.verifyThrottle.allowed = false
	d.verifyThrottle.retryAfter = 45 * time.Second

	err := svc.ResendVerification(context.Background(), "verify@example.com")
	var throttled *domain.ThrottledError
	require.ErrorAs(t, err, &throttled)
	require.ErrorIs(t, throttled.Err, domain.ErrVerificationCooldown)
	require.Equal(t, 45*time.Second, throttled.RetryAfter)
}

func TestResendVerification_DailyLimit(t *testing.T) {
	svc, d := newTestService()
	registerAndGet(t, svc, d, "verify@example.com")
	d.verifyThrottle.allowed = false
	d.verifyThrottle.dailyLimitReached = true

	err := svc.ResendVerification(context.Background(), "verify@example.com")
	var throttled *domain.ThrottledError
	require.ErrorAs(t, err, &throttled)
	require.ErrorIs(t, throttled.Err, domain.ErrVerificationDailyLimit)
}
