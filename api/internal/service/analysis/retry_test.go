package analysis_test

import (
	"context"
	"testing"

	"github.com/google/uuid"
	"github.com/stretchr/testify/require"

	"ai-vocal-coach/api/internal/domain"
)

func TestRetry_FailedAnalysis_Succeeds_Requeues(t *testing.T) {
	song := testSong()
	d := newTestService(t, song, 360, 20)
	ctx := context.Background()
	userID := uuid.New()

	created, _, err := d.svc.Enqueue(ctx, userID, song.ID, validWAVReader())
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

func TestRetry_NotFailed_ReturnsErrAnalysisNotFailed(t *testing.T) {
	song := testSong()
	d := newTestService(t, song, 360, 20)
	ctx := context.Background()
	userID := uuid.New()

	created, _, err := d.svc.Enqueue(ctx, userID, song.ID, validWAVReader())
	require.NoError(t, err)

	_, _, err = d.svc.Retry(ctx, created.ID, userID)
	require.ErrorIs(t, err, domain.ErrAnalysisNotFailed)
}

func TestRetry_WrongOwner_ReturnsErrNotFound(t *testing.T) {
	song := testSong()
	d := newTestService(t, song, 360, 20)
	ctx := context.Background()

	created, _, err := d.svc.Enqueue(ctx, uuid.New(), song.ID, validWAVReader())
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

	created, _, err := d.svc.Enqueue(ctx, userID, song.ID, validWAVReader())
	require.NoError(t, err)
	d.analyses.byID[created.ID].Status = domain.AnalysisStatusFailed
	d.queue.length = 20

	_, _, err = d.svc.Retry(ctx, created.ID, userID)
	require.ErrorIs(t, err, domain.ErrQueueFull)
}

func TestRetry_MovesToBackOfQueue(t *testing.T) {
	song := testSong()
	d := newTestService(t, song, 360, 20)
	ctx := context.Background()
	userID := uuid.New()

	first, _, err := d.svc.Enqueue(ctx, userID, song.ID, validWAVReader())
	require.NoError(t, err)
	second, _, err := d.svc.Enqueue(ctx, userID, song.ID, validWAVReader())
	require.NoError(t, err)

	d.analyses.byID[first.ID].Status = domain.AnalysisStatusFailed

	retried, positions, err := d.svc.Retry(ctx, first.ID, userID)
	require.NoError(t, err)
	require.Equal(t, 2, *retried.QueuePosition)
	require.Equal(t, 1, positions[second.ID])
	require.Equal(t, 2, positions[first.ID])
}
