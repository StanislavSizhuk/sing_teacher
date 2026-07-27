package auth_test

import (
	"context"
	"errors"
	"testing"

	"github.com/stretchr/testify/require"

	"ai-vocal-coach/api/internal/domain"
	"ai-vocal-coach/api/internal/service/auth"
)

func TestGoogleAuthStart_ProducesDistinctStateAndVerifier(t *testing.T) {
	svc, d := newTestService()

	authURL1, state1, verifier1, err := svc.GoogleAuthStart(context.Background())
	require.NoError(t, err)
	authURL2, state2, verifier2, err := svc.GoogleAuthStart(context.Background())
	require.NoError(t, err)

	require.NotEmpty(t, authURL1)
	require.NotEmpty(t, authURL2)
	require.NotEqual(t, state1, verifier1)
	require.NotEqual(t, state1, state2, "state must be freshly random every time")
	require.NotEqual(t, verifier1, verifier2, "PKCE verifier must be freshly random every time")
	require.Equal(t, 2, d.google.authCodeURLCalls)
}

func TestGoogleCallback_StateMismatch_NeverCallsExchange(t *testing.T) {
	svc, d := newTestService()

	_, err := svc.GoogleCallback(context.Background(), "code", "verifier", "attacker-state", "expected-state")
	require.ErrorIs(t, err, domain.ErrOAuthState)
	require.Zero(t, d.google.exchangeCalls)
}

func TestGoogleCallback_ExistingGoogleUser_LogsIn(t *testing.T) {
	svc, d := newTestService()
	ctx := context.Background()
	registerAndGet(t, svc, d, "existing@example.com")
	existing, err := d.users.GetByEmail(ctx, "existing@example.com")
	require.NoError(t, err)
	require.NoError(t, d.users.LinkGoogleID(ctx, existing.ID, "google-sub-1"))

	d.google.identity = auth.GoogleIdentity{Subject: "google-sub-1", Email: "existing@example.com", EmailVerified: true, Name: "Existing"}

	session, err := svc.GoogleCallback(ctx, "code", "verifier", "state", "state")
	require.NoError(t, err)
	require.NotEmpty(t, session.AccessToken)
	require.Contains(t, d.access.issued, existing.ID)
}

func TestGoogleCallback_UnverifiedGoogleEmail_Rejected(t *testing.T) {
	svc, d := newTestService()
	d.google.identity = auth.GoogleIdentity{Subject: "google-sub-new", Email: "new@example.com", EmailVerified: false}

	_, err := svc.GoogleCallback(context.Background(), "code", "verifier", "state", "state")
	require.ErrorIs(t, err, domain.ErrGoogleEmailNotVerified)
}

func TestGoogleCallback_LinksToExistingVerifiedEmail(t *testing.T) {
	svc, d := newTestService()
	ctx := context.Background()
	_, code := registerAndGet(t, svc, d, "shared@example.com")
	require.NoError(t, svc.VerifyEmail(ctx, "shared@example.com", code))
	existing, err := d.users.GetByEmail(ctx, "shared@example.com")
	require.NoError(t, err)

	d.google.identity = auth.GoogleIdentity{Subject: "google-sub-2", Email: "shared@example.com", EmailVerified: true, Name: "Shared"}

	session, err := svc.GoogleCallback(ctx, "code", "verifier", "state", "state")
	require.NoError(t, err)
	require.NotEmpty(t, session.AccessToken)

	linked, err := d.users.GetByGoogleID(ctx, "google-sub-2")
	require.NoError(t, err)
	require.Equal(t, existing.ID, linked.ID, "must link to the existing account, not create a new one")
}

func TestGoogleCallback_NewIdentity_CreatesAccount(t *testing.T) {
	svc, d := newTestService()
	d.google.identity = auth.GoogleIdentity{Subject: "google-sub-3", Email: "brandnew@example.com", EmailVerified: true, Name: "Brand New"}

	session, err := svc.GoogleCallback(context.Background(), "code", "verifier", "state", "state")
	require.NoError(t, err)
	require.NotEmpty(t, session.AccessToken)

	user, err := d.users.GetByEmail(context.Background(), "brandnew@example.com")
	require.NoError(t, err)
	require.True(t, user.EmailVerified, "Google already verified this address")
	require.NotNil(t, user.GoogleID)
	require.Equal(t, "google-sub-3", *user.GoogleID)
	require.Nil(t, user.PasswordHash)
}

func TestGoogleCallback_ExchangeFailure_Propagated(t *testing.T) {
	svc, d := newTestService()
	d.google.exchangeErr = errors.New("upstream unavailable")

	_, err := svc.GoogleCallback(context.Background(), "code", "verifier", "state", "state")
	require.Error(t, err)
}
