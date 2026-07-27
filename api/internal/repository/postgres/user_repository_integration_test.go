//go:build integration

package postgres_test

import (
	"context"
	"database/sql"
	"fmt"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"
	_ "github.com/jackc/pgx/v5/stdlib"
	"github.com/pressly/goose/v3"
	"github.com/stretchr/testify/require"
	"github.com/testcontainers/testcontainers-go"
	tcpostgres "github.com/testcontainers/testcontainers-go/modules/postgres"

	"ai-vocal-coach/api/internal/domain"
	"ai-vocal-coach/api/internal/repository/postgres"
	"ai-vocal-coach/api/migrations"
)

// setupPostgres starts a fresh postgres:16-alpine container per test, applies
// every goose migration exactly as production does, and returns a ready pool.
func setupPostgres(t *testing.T) *pgxpool.Pool {
	t.Helper()
	ctx := context.Background()

	container, err := tcpostgres.Run(ctx, "postgres:16-alpine",
		tcpostgres.WithDatabase("vocalcoach_test"),
		tcpostgres.WithUsername("test"),
		tcpostgres.WithPassword("test"),
		tcpostgres.BasicWaitStrategies(),
	)
	require.NoError(t, err)
	t.Cleanup(func() {
		require.NoError(t, testcontainers.TerminateContainer(container))
	})

	connStr, err := container.ConnectionString(ctx, "sslmode=disable")
	require.NoError(t, err)

	sqlDB, err := sql.Open("pgx", connStr)
	require.NoError(t, err)
	defer sqlDB.Close()

	goose.SetBaseFS(migrations.FS)
	require.NoError(t, goose.SetDialect("postgres"))
	require.NoError(t, goose.Up(sqlDB, "."))

	pool, err := pgxpool.New(ctx, connStr)
	require.NoError(t, err)
	t.Cleanup(pool.Close)

	return pool
}

func newTestUser(email string) *domain.User {
	hash := "argon2id-hash"
	return &domain.User{
		ID:           uuid.New(),
		Email:        email,
		PasswordHash: &hash,
		DisplayName:  "Test User",
	}
}

func TestUserRepository_CreateAndGetByEmail(t *testing.T) {
	pool := setupPostgres(t)
	repo := postgres.NewUserRepository(pool)
	ctx := context.Background()

	user := newTestUser(fmt.Sprintf("create-%s@example.com", uuid.NewString()))
	require.NoError(t, repo.Create(ctx, user))
	require.False(t, user.CreatedAt.IsZero())

	got, err := repo.GetByEmail(ctx, user.Email)
	require.NoError(t, err)
	require.Equal(t, user.ID, got.ID)
	require.False(t, got.EmailVerified)
}

func TestUserRepository_DuplicateEmail_Conflict(t *testing.T) {
	pool := setupPostgres(t)
	repo := postgres.NewUserRepository(pool)
	ctx := context.Background()

	email := fmt.Sprintf("dup-%s@example.com", uuid.NewString())
	require.NoError(t, repo.Create(ctx, newTestUser(email)))

	err := repo.Create(ctx, newTestUser(email))
	require.ErrorIs(t, err, domain.ErrEmailTaken)
}

func TestUserRepository_GetByID_NotFound(t *testing.T) {
	pool := setupPostgres(t)
	repo := postgres.NewUserRepository(pool)

	_, err := repo.GetByID(context.Background(), uuid.New())
	require.ErrorIs(t, err, domain.ErrNotFound)
}

func TestUserRepository_VerificationLifecycle(t *testing.T) {
	pool := setupPostgres(t)
	repo := postgres.NewUserRepository(pool)
	ctx := context.Background()

	user := newTestUser(fmt.Sprintf("verify-%s@example.com", uuid.NewString()))
	require.NoError(t, repo.Create(ctx, user))

	expiresAt := time.Now().Add(24 * time.Hour).Truncate(time.Microsecond)
	require.NoError(t, repo.UpdateVerificationCode(ctx, user.ID, "code-hash", expiresAt))

	attempts, err := repo.IncrementVerificationAttempts(ctx, user.ID)
	require.NoError(t, err)
	require.Equal(t, 1, attempts)

	require.NoError(t, repo.MarkVerified(ctx, user.ID))

	got, err := repo.GetByID(ctx, user.ID)
	require.NoError(t, err)
	require.True(t, got.EmailVerified)
	require.Nil(t, got.VerificationCodeHash)
	require.Equal(t, 0, got.VerificationAttempts)
}

func TestUserRepository_LinkGoogleID(t *testing.T) {
	pool := setupPostgres(t)
	repo := postgres.NewUserRepository(pool)
	ctx := context.Background()

	user := newTestUser(fmt.Sprintf("google-%s@example.com", uuid.NewString()))
	require.NoError(t, repo.Create(ctx, user))

	googleID := "google-sub-" + uuid.NewString()
	require.NoError(t, repo.LinkGoogleID(ctx, user.ID, googleID))

	got, err := repo.GetByGoogleID(ctx, googleID)
	require.NoError(t, err)
	require.Equal(t, user.ID, got.ID)
}

func TestUserRepository_HardDelete(t *testing.T) {
	pool := setupPostgres(t)
	repo := postgres.NewUserRepository(pool)
	ctx := context.Background()

	user := newTestUser(fmt.Sprintf("delete-%s@example.com", uuid.NewString()))
	require.NoError(t, repo.Create(ctx, user))
	require.NoError(t, repo.HardDelete(ctx, user.ID))

	_, err := repo.GetByID(ctx, user.ID)
	require.ErrorIs(t, err, domain.ErrNotFound)
}

func TestUserRepository_SoftDeleteExpiredUnverified(t *testing.T) {
	pool := setupPostgres(t)
	repo := postgres.NewUserRepository(pool)
	ctx := context.Background()

	expired := newTestUser(fmt.Sprintf("expired-%s@example.com", uuid.NewString()))
	require.NoError(t, repo.Create(ctx, expired))
	require.NoError(t, repo.UpdateVerificationCode(ctx, expired.ID, "hash", time.Now().Add(-time.Hour)))

	notYetExpired := newTestUser(fmt.Sprintf("fresh-%s@example.com", uuid.NewString()))
	require.NoError(t, repo.Create(ctx, notYetExpired))
	require.NoError(t, repo.UpdateVerificationCode(ctx, notYetExpired.ID, "hash", time.Now().Add(time.Hour)))

	count, err := repo.SoftDeleteExpiredUnverified(ctx)
	require.NoError(t, err)
	require.Equal(t, int64(1), count)

	_, err = repo.GetByID(ctx, expired.ID)
	require.ErrorIs(t, err, domain.ErrNotFound, "soft-deleted rows are excluded from GetByID")

	stillThere, err := repo.GetByID(ctx, notYetExpired.ID)
	require.NoError(t, err)
	require.NotNil(t, stillThere)
}
