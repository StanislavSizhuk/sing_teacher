package auth_test

import (
	"context"
	"testing"
	"time"

	"github.com/stretchr/testify/require"

	"ai-vocal-coach/api/internal/domain"
)

func TestLogin_Success(t *testing.T) {
	svc, d := newTestService()
	user, code := registerAndGet(t, svc, d, "login@example.com")
	require.NoError(t, svc.VerifyEmail(context.Background(), "login@example.com", code))

	session, err := svc.Login(context.Background(), "Login@Example.com", "correct-horse-battery", "1.2.3.4")
	require.NoError(t, err)
	require.NotEmpty(t, session.AccessToken)
	require.NotEmpty(t, session.RefreshToken)
	require.Contains(t, d.access.issued, user.ID)
	require.Equal(t, 1, d.loginThrottle.resets)
}

func TestLogin_WrongPassword_RecordsFailure(t *testing.T) {
	svc, d := newTestService()
	registerAndGet(t, svc, d, "login@example.com")

	_, err := svc.Login(context.Background(), "login@example.com", "wrong password entirely", "1.2.3.4")
	require.ErrorIs(t, err, domain.ErrInvalidCredentials)
	require.Equal(t, 1, d.loginThrottle.failures)
}

func TestLogin_UnknownEmail_SameErrorAsWrongPassword(t *testing.T) {
	svc, d := newTestService()

	_, err := svc.Login(context.Background(), "ghost@example.com", "whatever-password", "1.2.3.4")
	require.ErrorIs(t, err, domain.ErrInvalidCredentials, "must not leak that the account does not exist")
	require.Equal(t, 1, d.loginThrottle.failures, "an unknown email still counts as a failed attempt")
}

func TestLogin_NotVerified_SafeToDiscloseAfterCorrectPassword(t *testing.T) {
	svc, d := newTestService()
	registerAndGet(t, svc, d, "login@example.com")

	_, err := svc.Login(context.Background(), "login@example.com", "correct-horse-battery", "1.2.3.4")
	require.ErrorIs(t, err, domain.ErrEmailNotVerified)
	require.Zero(t, d.loginThrottle.failures, "a correct password must not count as a brute-force failure")
}

func TestLogin_Locked_SkipsCredentialCheck(t *testing.T) {
	svc, d := newTestService()
	registerAndGet(t, svc, d, "login@example.com")
	d.users.getByEmailCalls = 0 // reset the lookup registerAndGet performed
	d.loginThrottle.locked = true
	d.loginThrottle.retryAfter = 10 * time.Minute

	_, err := svc.Login(context.Background(), "login@example.com", "correct-horse-battery", "1.2.3.4")
	var throttled *domain.ThrottledError
	require.ErrorAs(t, err, &throttled)
	require.ErrorIs(t, throttled.Err, domain.ErrAccountLocked)
	require.Equal(t, 10*time.Minute, throttled.RetryAfter)
	require.Zero(t, d.users.getByEmailCalls, "a locked caller should never even reach the credential check")
}
