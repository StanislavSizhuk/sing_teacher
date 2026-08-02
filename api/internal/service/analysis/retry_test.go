package analysis_test

import (
	"context"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/stretchr/testify/require"

	"ai-vocal-coach/api/internal/domain"
)

func TestRetry_FailedAnalysis_Succeeds_Requeues(t *testing.T) {
	song := testSong()
	d := newTestService(t, song, 360, 20)
	ctx := context.Background()
	userID := uuid.New()

	created, _, err := d.svc.Enqueue(ctx, userID, song.ID, domain.AnalysisModeClean, false, validWAVReader())
	require.NoError(t, err)
	errCode := "INTERNAL"
	d.analyses.byID[created.ID].Status = domain.AnalysisStatusFailed
	d.analyses.byID[created.ID].ErrorCode = &errCode

	retried, positions, err := d.svc.Retry(ctx, created.ID, userID)
	require.NoError(t, err)
	require.Equal(t, domain.AnalysisStatusQueued, retried.Status)
	require.Nil(t, retried.ErrorCode)
	require.NotNil(t, retried.QueuePosition)
	require.Equal(t, 1, positions[created.ID])
	require.Len(t, d.queue.enqueued, 2, "retry must publish a fresh queue entry")
}

func TestRetry_ResetsQueuedAt_NotOriginalCreatedAt(t *testing.T) {
	song := testSong()
	d := newTestService(t, song, 360, 20)
	ctx := context.Background()
	userID := uuid.New()

	created, _, err := d.svc.Enqueue(ctx, userID, song.ID, domain.AnalysisModeClean, false, validWAVReader())
	require.NoError(t, err)
	staleCreatedAt := time.Now().Add(-9 * time.Hour)
	d.analyses.byID[created.ID].CreatedAt = staleCreatedAt
	d.analyses.byID[created.ID].QueuedAt = staleCreatedAt
	d.analyses.byID[created.ID].Status = domain.AnalysisStatusFailed

	before := time.Now()
	retried, _, err := d.svc.Retry(ctx, created.ID, userID)
	require.NoError(t, err)

	// The bug this guards: QueueStatus.tsx's live wait timer reads
	// QueuedAt, not CreatedAt (spec 10, FR-22) -- a retry that left
	// QueuedAt at its original, hours-old value made a fresh retry render
	// as "waiting 9h" instead of a few seconds.
	require.False(t, retried.QueuedAt.Before(before), "QueuedAt must be reset to the retry time")
	require.Equal(t, staleCreatedAt, retried.CreatedAt, "CreatedAt (original submission) must stay untouched")
}

func TestRetry_NotFailed_ReturnsErrAnalysisNotFailed(t *testing.T) {
	song := testSong()
	d := newTestService(t, song, 360, 20)
	ctx := context.Background()
	userID := uuid.New()

	created, _, err := d.svc.Enqueue(ctx, userID, song.ID, domain.AnalysisModeClean, false, validWAVReader())
	require.NoError(t, err)

	_, _, err = d.svc.Retry(ctx, created.ID, userID)
	require.ErrorIs(t, err, domain.ErrAnalysisNotFailed)
}

func TestRetry_WrongOwner_ReturnsErrNotFound(t *testing.T) {
	song := testSong()
	d := newTestService(t, song, 360, 20)
	ctx := context.Background()

	created, _, err := d.svc.Enqueue(ctx, uuid.New(), song.ID, domain.AnalysisModeClean, false, validWAVReader())
	require.NoError(t, err)
	d.analyses.byID[created.ID].Status = domain.AnalysisStatusFailed

	_, _, err = d.svc.Retry(ctx, created.ID, uuid.New())
	require.ErrorIs(t, err, domain.ErrNotFound)
}

func TestRetry_QueueFull_Rejected(t *testing.T) {
	song := testSong()
	d := newTestService(t, song, 360, 20)
	ctx := context.Background()
	userID := uuid.New()

	created, _, err := d.svc.Enqueue(ctx, userID, song.ID, domain.AnalysisModeClean, false, validWAVReader())
	require.NoError(t, err)
	d.analyses.byID[created.ID].Status = domain.AnalysisStatusFailed
	d.queue.length = 20

	_, _, err = d.svc.Retry(ctx, created.ID, userID)
	require.ErrorIs(t, err, domain.ErrQueueFull)
}

func TestRetry_SongPrepFailed_ReturnsErrReferencePrepFailed(t *testing.T) {
	song := testSong()
	d := newTestService(t, song, 360, 20)
	ctx := context.Background()
	userID := uuid.New()

	created, _, err := d.svc.Enqueue(ctx, userID, song.ID, domain.AnalysisModeClean, false, validWAVReader())
	require.NoError(t, err)
	d.analyses.byID[created.ID].Status = domain.AnalysisStatusFailed
	// The song's own cold path failed after the analysis was created --
	// e.g. queue/prep_handler.py's fail_waiting_for_reference carried this
	// analysis down with it (spec 6.2, FR-17).
	d.songs.byID[song.ID].PrepStatus = domain.SongPrepFailed

	_, _, err = d.svc.Retry(ctx, created.ID, userID)
	require.ErrorIs(t, err, domain.ErrReferencePrepFailed)
	require.Equal(t, domain.AnalysisStatusFailed, d.analyses.byID[created.ID].Status,
		"a retry rejected outright must not mutate the row")
}

func TestRetry_SongNotReadyYet_WaitsInsteadOfQueueing(t *testing.T) {
	song := waitingSong()
	d := newTestService(t, song, 360, 20)
	ctx := context.Background()
	userID := uuid.New()

	created, _, err := d.svc.Enqueue(ctx, userID, song.ID, domain.AnalysisModeClean, false, validWAVReader())
	require.NoError(t, err)
	require.Equal(t, domain.AnalysisStatusWaitingForReference, created.Status)
	d.analyses.byID[created.ID].Status = domain.AnalysisStatusFailed

	retried, positions, err := d.svc.Retry(ctx, created.ID, userID)
	require.NoError(t, err)
	require.Equal(t, domain.AnalysisStatusWaitingForReference, retried.Status)
	require.Nil(t, retried.ErrorCode)
	require.Nil(t, positions)
	require.Empty(t, d.queue.enqueued, "must not publish onto analyses:run before the song is ready")
}

func TestRetry_MovesToBackOfQueue(t *testing.T) {
	song := testSong()
	d := newTestService(t, song, 360, 20)
	ctx := context.Background()
	userID := uuid.New()

	first, _, err := d.svc.Enqueue(ctx, userID, song.ID, domain.AnalysisModeClean, false, validWAVReader())
	require.NoError(t, err)
	second, _, err := d.svc.Enqueue(ctx, userID, song.ID, domain.AnalysisModeClean, false, validWAVReader())
	require.NoError(t, err)

	d.analyses.byID[first.ID].Status = domain.AnalysisStatusFailed

	retried, positions, err := d.svc.Retry(ctx, first.ID, userID)
	require.NoError(t, err)
	require.Equal(t, 2, *retried.QueuePosition)
	require.Equal(t, 1, positions[second.ID])
	require.Equal(t, 2, positions[first.ID])
}
