//go:build integration

package postgres_test

import (
	"context"
	"fmt"
	"testing"

	"github.com/google/uuid"
	"github.com/stretchr/testify/require"

	"ai-vocal-coach/api/internal/domain"
	"ai-vocal-coach/api/internal/repository/postgres"
)

func TestAnalysisRepository_Create_AssignsQueueSeqAndCreatedAt(t *testing.T) {
	pool := setupPostgres(t)
	ctx := context.Background()
	userRepo := postgres.NewUserRepository(pool)
	songRepo := postgres.NewSongRepository(pool)
	analysisRepo := postgres.NewAnalysisRepository(pool)

	user := newTestUser(fmt.Sprintf("analysis-create-%s@example.com", uuid.NewString()))
	require.NoError(t, userRepo.Create(ctx, user))
	song, _, err := songRepo.GetOrCreate(ctx, newTestSong(fmt.Sprintf("hash-%s", uuid.NewString())))
	require.NoError(t, err)

	a := &domain.Analysis{ID: uuid.New(), UserID: user.ID, SongID: song.ID, Status: domain.AnalysisStatusQueued}
	require.NoError(t, analysisRepo.Create(ctx, a))
	require.False(t, a.CreatedAt.IsZero())
	require.Positive(t, a.QueueSeq)
}

func TestAnalysisRepository_GetByID_ScopedToOwner(t *testing.T) {
	pool := setupPostgres(t)
	ctx := context.Background()
	userRepo := postgres.NewUserRepository(pool)
	songRepo := postgres.NewSongRepository(pool)
	analysisRepo := postgres.NewAnalysisRepository(pool)

	owner := newTestUser(fmt.Sprintf("owner-%s@example.com", uuid.NewString()))
	stranger := newTestUser(fmt.Sprintf("stranger-%s@example.com", uuid.NewString()))
	require.NoError(t, userRepo.Create(ctx, owner))
	require.NoError(t, userRepo.Create(ctx, stranger))
	song, _, err := songRepo.GetOrCreate(ctx, newTestSong(fmt.Sprintf("hash-%s", uuid.NewString())))
	require.NoError(t, err)

	a := &domain.Analysis{ID: uuid.New(), UserID: owner.ID, SongID: song.ID, Status: domain.AnalysisStatusQueued}
	require.NoError(t, analysisRepo.Create(ctx, a))

	got, err := analysisRepo.GetByID(ctx, a.ID, owner.ID)
	require.NoError(t, err)
	require.Equal(t, a.ID, got.ID)

	_, err = analysisRepo.GetByID(ctx, a.ID, stranger.ID)
	require.ErrorIs(t, err, domain.ErrNotFound, "a different user's analysis must look like it doesn't exist")
}

func TestAnalysisRepository_Cancel_QueuedAnalysis_Succeeds(t *testing.T) {
	pool := setupPostgres(t)
	ctx := context.Background()
	userRepo := postgres.NewUserRepository(pool)
	songRepo := postgres.NewSongRepository(pool)
	analysisRepo := postgres.NewAnalysisRepository(pool)

	user := newTestUser(fmt.Sprintf("cancel-%s@example.com", uuid.NewString()))
	require.NoError(t, userRepo.Create(ctx, user))
	song, _, err := songRepo.GetOrCreate(ctx, newTestSong(fmt.Sprintf("hash-%s", uuid.NewString())))
	require.NoError(t, err)

	a := &domain.Analysis{ID: uuid.New(), UserID: user.ID, SongID: song.ID, Status: domain.AnalysisStatusQueued}
	require.NoError(t, analysisRepo.Create(ctx, a))

	canceled, err := analysisRepo.Cancel(ctx, a.ID, user.ID)
	require.NoError(t, err)
	require.Equal(t, domain.AnalysisStatusCanceled, canceled.Status)
}

func TestAnalysisRepository_Cancel_AlreadyCanceled_ReturnsErrAnalysisNotQueued(t *testing.T) {
	pool := setupPostgres(t)
	ctx := context.Background()
	userRepo := postgres.NewUserRepository(pool)
	songRepo := postgres.NewSongRepository(pool)
	analysisRepo := postgres.NewAnalysisRepository(pool)

	user := newTestUser(fmt.Sprintf("cancel-twice-%s@example.com", uuid.NewString()))
	require.NoError(t, userRepo.Create(ctx, user))
	song, _, err := songRepo.GetOrCreate(ctx, newTestSong(fmt.Sprintf("hash-%s", uuid.NewString())))
	require.NoError(t, err)

	a := &domain.Analysis{ID: uuid.New(), UserID: user.ID, SongID: song.ID, Status: domain.AnalysisStatusQueued}
	require.NoError(t, analysisRepo.Create(ctx, a))
	_, err = analysisRepo.Cancel(ctx, a.ID, user.ID)
	require.NoError(t, err)

	_, err = analysisRepo.Cancel(ctx, a.ID, user.ID)
	require.ErrorIs(t, err, domain.ErrAnalysisNotQueued)
}

func TestAnalysisRepository_Cancel_WrongOwner_ReturnsErrNotFound(t *testing.T) {
	pool := setupPostgres(t)
	ctx := context.Background()
	userRepo := postgres.NewUserRepository(pool)
	songRepo := postgres.NewSongRepository(pool)
	analysisRepo := postgres.NewAnalysisRepository(pool)

	owner := newTestUser(fmt.Sprintf("owner2-%s@example.com", uuid.NewString()))
	stranger := newTestUser(fmt.Sprintf("stranger2-%s@example.com", uuid.NewString()))
	require.NoError(t, userRepo.Create(ctx, owner))
	require.NoError(t, userRepo.Create(ctx, stranger))
	song, _, err := songRepo.GetOrCreate(ctx, newTestSong(fmt.Sprintf("hash-%s", uuid.NewString())))
	require.NoError(t, err)

	a := &domain.Analysis{ID: uuid.New(), UserID: owner.ID, SongID: song.ID, Status: domain.AnalysisStatusQueued}
	require.NoError(t, analysisRepo.Create(ctx, a))

	_, err = analysisRepo.Cancel(ctx, a.ID, stranger.ID)
	require.ErrorIs(t, err, domain.ErrNotFound)
}

func TestAnalysisRepository_SetQueueStreamID_Persists(t *testing.T) {
	pool := setupPostgres(t)
	ctx := context.Background()
	userRepo := postgres.NewUserRepository(pool)
	songRepo := postgres.NewSongRepository(pool)
	analysisRepo := postgres.NewAnalysisRepository(pool)

	user := newTestUser(fmt.Sprintf("stream-id-%s@example.com", uuid.NewString()))
	require.NoError(t, userRepo.Create(ctx, user))
	song, _, err := songRepo.GetOrCreate(ctx, newTestSong(fmt.Sprintf("hash-%s", uuid.NewString())))
	require.NoError(t, err)

	a := &domain.Analysis{ID: uuid.New(), UserID: user.ID, SongID: song.ID, Status: domain.AnalysisStatusQueued}
	require.NoError(t, analysisRepo.Create(ctx, a))
	require.NoError(t, analysisRepo.SetQueueStreamID(ctx, a.ID, "1234-0"))

	got, err := analysisRepo.GetByID(ctx, a.ID, user.ID)
	require.NoError(t, err)
	require.NotNil(t, got.QueueStreamID)
	require.Equal(t, "1234-0", *got.QueueStreamID)
}

func TestAnalysisRepository_RecalculatePositions_AssignsFIFOOrder(t *testing.T) {
	pool := setupPostgres(t)
	ctx := context.Background()
	userRepo := postgres.NewUserRepository(pool)
	songRepo := postgres.NewSongRepository(pool)
	analysisRepo := postgres.NewAnalysisRepository(pool)

	user := newTestUser(fmt.Sprintf("fifo-%s@example.com", uuid.NewString()))
	require.NoError(t, userRepo.Create(ctx, user))
	song, _, err := songRepo.GetOrCreate(ctx, newTestSong(fmt.Sprintf("hash-%s", uuid.NewString())))
	require.NoError(t, err)

	var ids []uuid.UUID
	for i := 0; i < 3; i++ {
		a := &domain.Analysis{ID: uuid.New(), UserID: user.ID, SongID: song.ID, Status: domain.AnalysisStatusQueued}
		require.NoError(t, analysisRepo.Create(ctx, a))
		ids = append(ids, a.ID)
	}

	positions, err := analysisRepo.RecalculatePositions(ctx)
	require.NoError(t, err)
	require.Equal(t, 1, positions[ids[0]])
	require.Equal(t, 2, positions[ids[1]])
	require.Equal(t, 3, positions[ids[2]])
}

func TestAnalysisRepository_RecalculatePositions_AfterCancel_ShiftsRemaining(t *testing.T) {
	pool := setupPostgres(t)
	ctx := context.Background()
	userRepo := postgres.NewUserRepository(pool)
	songRepo := postgres.NewSongRepository(pool)
	analysisRepo := postgres.NewAnalysisRepository(pool)

	user := newTestUser(fmt.Sprintf("shift-%s@example.com", uuid.NewString()))
	require.NoError(t, userRepo.Create(ctx, user))
	song, _, err := songRepo.GetOrCreate(ctx, newTestSong(fmt.Sprintf("hash-%s", uuid.NewString())))
	require.NoError(t, err)

	var ids []uuid.UUID
	for i := 0; i < 3; i++ {
		a := &domain.Analysis{ID: uuid.New(), UserID: user.ID, SongID: song.ID, Status: domain.AnalysisStatusQueued}
		require.NoError(t, analysisRepo.Create(ctx, a))
		ids = append(ids, a.ID)
	}
	_, err = analysisRepo.RecalculatePositions(ctx)
	require.NoError(t, err)

	_, err = analysisRepo.Cancel(ctx, ids[0], user.ID)
	require.NoError(t, err)

	changed, err := analysisRepo.RecalculatePositions(ctx)
	require.NoError(t, err)
	// The canceled job is no longer queued, so it must not appear in the
	// changed set at all; the two behind it must each move up by one.
	_, stillPresent := changed[ids[0]]
	require.False(t, stillPresent)
	require.Equal(t, 1, changed[ids[1]])
	require.Equal(t, 2, changed[ids[2]])
}
