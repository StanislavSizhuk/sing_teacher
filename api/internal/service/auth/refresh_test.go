package auth_test

import (
	"context"
	"testing"

	"github.com/google/uuid"
	"github.com/stretchr/testify/require"

	"ai-vocal-coach/api/internal/domain"
)

func TestRefresh_Success(t *testing.T) {
	svc, d := newTestService()
	wantUser := uuid.New()
	d.tokens.rotateFunc = func(context.Context, string) (string, uuid.UUID, error) {
		return "new-refresh-token", wantUser, nil
	}

	session, err := svc.Refresh(context.Background(), "old-refresh-token")
	require.NoError(t, err)
	require.Equal(t, "new-refresh-token", session.RefreshToken)
	require.Contains(t, d.access.issued, wantUser)
}

func TestRefresh_Reused_PropagatesError(t *testing.T) {
	svc, d := newTestService()
	d.tokens.rotateFunc = func(context.Context, string) (string, uuid.UUID, error) {
		return "", uuid.Nil, domain.ErrRefreshTokenReused
	}

	_, err := svc.Refresh(context.Background(), "stolen-token")
	require.ErrorIs(t, err, domain.ErrRefreshTokenReused)
}

func TestRefresh_Invalid_PropagatesError(t *testing.T) {
	svc, d := newTestService()
	d.tokens.rotateFunc = func(context.Context, string) (string, uuid.UUID, error) {
		return "", uuid.Nil, domain.ErrRefreshTokenInvalid
	}

	_, err := svc.Refresh(context.Background(), "garbage")
	require.ErrorIs(t, err, domain.ErrRefreshTokenInvalid)
}

func TestLogout_RevokesPresentedToken(t *testing.T) {
	svc, d := newTestService()
	err := svc.Logout(context.Background(), "my-refresh-token")
	require.NoError(t, err)
	require.Equal(t, []string{"my-refresh-token"}, d.tokens.revoked)
}

func TestLogoutAll_RevokesEveryFamilyForUser(t *testing.T) {
	svc, d := newTestService()
	userID := uuid.New()
	err := svc.LogoutAll(context.Background(), userID)
	require.NoError(t, err)
	require.Equal(t, []uuid.UUID{userID}, d.tokens.revokedUsers)
}
