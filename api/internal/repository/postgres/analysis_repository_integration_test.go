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
	_, err = pool.Exec(ctx, `UPDATE analyses SET queue_position = 4 WHERE id = $1`, a.ID)
	require.NoError(t, err)

	canceled, err := analysisRepo.Cancel(ctx, a.ID, user.ID)
	require.NoError(t, err)
	require.Equal(t, domain.AnalysisStatusCanceled, canceled.Status)
	// spec 8.2: queue_position is "Absent once no longer queued".
	require.Nil(t, canceled.QueuePosition)
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

func TestAnalysisRepository_Retry_FailedAnalysis_Succeeds(t *testing.T) {
	pool := setupPostgres(t)
	ctx := context.Background()
	userRepo := postgres.NewUserRepository(pool)
	songRepo := postgres.NewSongRepository(pool)
	analysisRepo := postgres.NewAnalysisRepository(pool)

	user := newTestUser(fmt.Sprintf("retry-%s@example.com", uuid.NewString()))
	require.NoError(t, userRepo.Create(ctx, user))
	song, _, err := songRepo.GetOrCreate(ctx, newTestSong(fmt.Sprintf("hash-%s", uuid.NewString())))
	require.NoError(t, err)

	a := &domain.Analysis{ID: uuid.New(), UserID: user.ID, SongID: song.ID, Status: domain.AnalysisStatusQueued}
	require.NoError(t, analysisRepo.Create(ctx, a))
	require.NoError(t, analysisRepo.SetQueueStreamID(ctx, a.ID, "1234-0"))

	_, err = pool.Exec(ctx, `UPDATE analyses SET status = 'failed', error_code = 'INTERNAL',
		current_stage = 'pitch', current_stage_index = 5, total_stages = 11,
		current_stage_started_at = now() WHERE id = $1`, a.ID)
	require.NoError(t, err)

	retried, err := analysisRepo.Retry(ctx, a.ID, user.ID)
	require.NoError(t, err)
	require.Equal(t, domain.AnalysisStatusQueued, retried.Status)
	require.Nil(t, retried.ErrorCode)
	require.Nil(t, retried.CurrentStage)
	require.Nil(t, retried.CurrentStageIndex)
	require.Nil(t, retried.TotalStages)
	require.Nil(t, retried.CurrentStageStartedAt)
	require.Nil(t, retried.QueueStreamID)
	require.Greater(t, retried.QueueSeq, a.QueueSeq, "retry must draw a fresh, later queue_seq so it goes to the back of the FIFO")
}

func TestAnalysisRepository_Retry_NotFailed_ReturnsErrAnalysisNotFailed(t *testing.T) {
	pool := setupPostgres(t)
	ctx := context.Background()
	userRepo := postgres.NewUserRepository(pool)
	songRepo := postgres.NewSongRepository(pool)
	analysisRepo := postgres.NewAnalysisRepository(pool)

	user := newTestUser(fmt.Sprintf("retry-not-failed-%s@example.com", uuid.NewString()))
	require.NoError(t, userRepo.Create(ctx, user))
	song, _, err := songRepo.GetOrCreate(ctx, newTestSong(fmt.Sprintf("hash-%s", uuid.NewString())))
	require.NoError(t, err)

	a := &domain.Analysis{ID: uuid.New(), UserID: user.ID, SongID: song.ID, Status: domain.AnalysisStatusQueued}
	require.NoError(t, analysisRepo.Create(ctx, a))

	_, err = analysisRepo.Retry(ctx, a.ID, user.ID)
	require.ErrorIs(t, err, domain.ErrAnalysisNotFailed)
}

func TestAnalysisRepository_Retry_WrongOwner_ReturnsErrNotFound(t *testing.T) {
	pool := setupPostgres(t)
	ctx := context.Background()
	userRepo := postgres.NewUserRepository(pool)
	songRepo := postgres.NewSongRepository(pool)
	analysisRepo := postgres.NewAnalysisRepository(pool)

	owner := newTestUser(fmt.Sprintf("retry-owner-%s@example.com", uuid.NewString()))
	stranger := newTestUser(fmt.Sprintf("retry-stranger-%s@example.com", uuid.NewString()))
	require.NoError(t, userRepo.Create(ctx, owner))
	require.NoError(t, userRepo.Create(ctx, stranger))
	song, _, err := songRepo.GetOrCreate(ctx, newTestSong(fmt.Sprintf("hash-%s", uuid.NewString())))
	require.NoError(t, err)

	a := &domain.Analysis{ID: uuid.New(), UserID: owner.ID, SongID: song.ID, Status: domain.AnalysisStatusQueued}
	require.NoError(t, analysisRepo.Create(ctx, a))
	_, err = pool.Exec(ctx, `UPDATE analyses SET status = 'failed' WHERE id = $1`, a.ID)
	require.NoError(t, err)

	_, err = analysisRepo.Retry(ctx, a.ID, stranger.ID)
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
