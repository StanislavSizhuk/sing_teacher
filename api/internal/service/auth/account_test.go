package auth_test

import (
	"context"
	"testing"
	"time"

	"github.com/stretchr/testify/require"

	"ai-vocal-coach/api/internal/domain"
)

func TestGetProfile_ReturnsAccount(t *testing.T) {
	svc, d := newTestService()
	user, _ := registerAndGet(t, svc, d, "profile@example.com")

	got, err := svc.GetProfile(context.Background(), user.ID)
	require.NoError(t, err)
	require.Equal(t, user.Email, got.Email)
}

func TestDeleteAccount_RevokesSessionsAndDeletes(t *testing.T) {
	svc, d := newTestService()
	user, _ := registerAndGet(t, svc, d, "delete-me@example.com")

	err := svc.DeleteAccount(context.Background(), user.ID)
	require.NoError(t, err)

	require.Contains(t, d.tokens.revokedUsers, user.ID, "every session must be revoked on account deletion")
	_, err = d.users.GetByID(context.Background(), user.ID)
	require.ErrorIs(t, err, domain.ErrNotFound, "the account itself must be gone")
}

func TestCleanupExpiredUnverifiedAccounts_DelegatesToRepository(t *testing.T) {
	svc, d := newTestService()
	registerAndGet(t, svc, d, "expired@example.com")
	user, err := d.users.GetByEmail(context.Background(), "expired@example.com")
	require.NoError(t, err)

	past := d.clock.now.Add(-time.Hour)
	require.NoError(t, d.users.UpdateVerificationCode(context.Background(), user.ID, "irrelevant-hash", past))

	count, err := svc.CleanupExpiredUnverifiedAccounts(context.Background())
	require.NoError(t, err)
	require.Equal(t, int64(1), count)
}
