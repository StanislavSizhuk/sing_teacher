package auth_test

import (
	"context"
	"testing"

	"github.com/stretchr/testify/require"

	"ai-vocal-coach/api/internal/domain"
)

func TestRegister_RejectsWeakPasswords(t *testing.T) {
	cases := []struct {
		name     string
		password string
	}{
		{"too short", "short1"},
		{"exactly 9 chars", "123456789"},
		{"common password", "password123"},
		{"common password different case", "PASSWORD1"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			svc, d := newTestService()
			err := svc.Register(context.Background(), "new@example.com", tc.password, "New User")
			require.ErrorIs(t, err, domain.ErrWeakPassword)
			require.Empty(t, d.mailer.sent, "no email should be sent for a rejected password")
			_, err = d.users.GetByEmail(context.Background(), "new@example.com")
			require.ErrorIs(t, err, domain.ErrNotFound, "no account should be created")
		})
	}
}

func TestRegister_NewAccount_CreatesUserAndSendsCode(t *testing.T) {
	svc, d := newTestService()

	err := svc.Register(context.Background(), "New@Example.com", "correct-horse-battery", "New User")
	require.NoError(t, err)

	user, err := d.users.GetByEmail(context.Background(), "new@example.com")
	require.NoError(t, err, "email should be normalized to lowercase")
	require.False(t, user.EmailVerified)
	require.NotNil(t, user.PasswordHash)
	require.NotNil(t, user.VerificationCodeHash)
	require.NotNil(t, user.VerificationExpiresAt)

	require.Len(t, d.mailer.sent, 1)
	require.Equal(t, "new@example.com", d.mailer.sent[0].To)
	require.Len(t, d.mailer.sent[0].Code, 6)
}

func TestRegister_ExistingUnverifiedAccount_DoesNotReveal_ButResendsCode(t *testing.T) {
	svc, d := newTestService()
	ctx := context.Background()
	require.NoError(t, svc.Register(ctx, "dup@example.com", "correct-horse-battery", "First"))
	require.Len(t, d.mailer.sent, 1)

	err := svc.Register(ctx, "dup@example.com", "another-strong-pass", "Second")
	require.NoError(t, err, "must look identical to a fresh registration")

	require.Len(t, d.mailer.sent, 2, "resend should have gone out for the still-unverified account")

	users := 0
	for range d.users.byID {
		users++
	}
	require.Equal(t, 1, users, "no second account should have been created")
}

func TestRegister_ExistingVerifiedAccount_DoesNotReveal_NoEmailSent(t *testing.T) {
	svc, d := newTestService()
	ctx := context.Background()
	require.NoError(t, svc.Register(ctx, "verified@example.com", "correct-horse-battery", "First"))
	user, err := d.users.GetByEmail(ctx, "verified@example.com")
	require.NoError(t, err)
	require.NoError(t, d.users.MarkVerified(ctx, user.ID))

	err = svc.Register(ctx, "verified@example.com", "another-strong-pass", "Second")
	require.NoError(t, err)
	require.Len(t, d.mailer.sent, 1, "only the original registration email, nothing new")
}

func TestRegister_CreateRace_StillReturnsNil(t *testing.T) {
	svc, d := newTestService()
	d.users.createErr = domain.ErrEmailTaken

	err := svc.Register(context.Background(), "race@example.com", "correct-horse-battery", "Racer")
	require.NoError(t, err, "losing a create race must not be distinguishable from success")
}
