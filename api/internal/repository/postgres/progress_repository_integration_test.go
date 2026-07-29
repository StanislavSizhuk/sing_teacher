//go:build integration

package postgres_test

import (
	"context"
	"fmt"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/stretchr/testify/require"

	"ai-vocal-coach/api/internal/domain"
	"ai-vocal-coach/api/internal/repository/postgres"
)

// insertProgressSnapshot writes a row directly with SQL: only the E3 worker
// (worker/src/vocalcoach/repositories/postgres.py) ever inserts
// progress_snapshots in production, so there is no Go repository method to
// reuse here.
func insertProgressSnapshot(
	t *testing.T, pool *pgxpool.Pool, userID, analysisID uuid.UUID, overallScore float64, createdAt time.Time,
) {
	t.Helper()
	_, err := pool.Exec(context.Background(),
		`INSERT INTO progress_snapshots (user_id, analysis_id, overall_score, created_at)
		 VALUES ($1, $2, $3, $4)`,
		userID, analysisID, overallScore, createdAt)
	require.NoError(t, err)
}

func TestProgressRepository_ListByUser_OrdersOldestFirstAndScopesToOwner(t *testing.T) {
	pool := setupPostgres(t)
	ctx := context.Background()
	userRepo := postgres.NewUserRepository(pool)
	songRepo := postgres.NewSongRepository(pool)
	analysisRepo := postgres.NewAnalysisRepository(pool)
	progressRepo := postgres.NewProgressRepository(pool)

	owner := newTestUser(fmt.Sprintf("progress-owner-%s@example.com", uuid.NewString()))
	stranger := newTestUser(fmt.Sprintf("progress-stranger-%s@example.com", uuid.NewString()))
	require.NoError(t, userRepo.Create(ctx, owner))
	require.NoError(t, userRepo.Create(ctx, stranger))
	song, _, err := songRepo.GetOrCreate(ctx, newTestSong(fmt.Sprintf("hash-%s", uuid.NewString())))
	require.NoError(t, err)

	older := &domain.Analysis{ID: uuid.New(), UserID: owner.ID, SongID: song.ID, Status: domain.AnalysisStatusQueued}
	newer := &domain.Analysis{ID: uuid.New(), UserID: owner.ID, SongID: song.ID, Status: domain.AnalysisStatusQueued}
	someoneElses := &domain.Analysis{ID: uuid.New(), UserID: stranger.ID, SongID: song.ID, Status: domain.AnalysisStatusQueued}
	require.NoError(t, analysisRepo.Create(ctx, older))
	require.NoError(t, analysisRepo.Create(ctx, newer))
	require.NoError(t, analysisRepo.Create(ctx, someoneElses))

	base := time.Now().UTC().Truncate(time.Second)
	insertProgressSnapshot(t, pool, owner.ID, older.ID, 60, base)
	insertProgressSnapshot(t, pool, owner.ID, newer.ID, 75, base.Add(time.Hour))
	insertProgressSnapshot(t, pool, stranger.ID, someoneElses.ID, 99, base)

	points, err := progressRepo.ListByUser(ctx, owner.ID)
	require.NoError(t, err)
	require.Len(t, points, 2)
	require.Equal(t, older.ID, points[0].AnalysisID)
	require.InDelta(t, 60, points[0].OverallScore, 0.001)
	require.Equal(t, newer.ID, points[1].AnalysisID)
	require.InDelta(t, 75, points[1].OverallScore, 0.001)
}

func TestProgressRepository_ListByUser_NoSnapshots_ReturnsEmptyNotNil(t *testing.T) {
	pool := setupPostgres(t)
	ctx := context.Background()
	userRepo := postgres.NewUserRepository(pool)
	progressRepo := postgres.NewProgressRepository(pool)

	user := newTestUser(fmt.Sprintf("progress-empty-%s@example.com", uuid.NewString()))
	require.NoError(t, userRepo.Create(ctx, user))

	points, err := progressRepo.ListByUser(ctx, user.ID)
	require.NoError(t, err)
	require.NotNil(t, points)
	require.Empty(t, points)
}
