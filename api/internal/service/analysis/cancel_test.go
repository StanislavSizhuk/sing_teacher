package analysis_test

import (
	"context"
	"testing"

	"github.com/google/uuid"
	"github.com/stretchr/testify/require"

	"ai-vocal-coach/api/internal/domain"
)

func TestCancel_QueuedAnalysis_Succeeds_RemovesFromQueue(t *testing.T) {
	song := testSong()
	d := newTestService(t, song, 360, 20)
	ctx := context.Background()
	userID := uuid.New()

	created, _, err := d.svc.Enqueue(ctx, userID, song.ID, domain.AnalysisModeClean, false, domain.LocaleEN, validWAVReader())
	require.NoError(t, err)

	canceled, _, err := d.svc.Cancel(ctx, created.ID, userID)
	require.NoError(t, err)
	require.Equal(t, domain.AnalysisStatusCanceled, canceled.Status)
	require.Contains(t, d.queue.removed, *created.QueueStreamID)
}

func TestCancel_NotQueued_ReturnsErrAnalysisNotQueued(t *testing.T) {
	song := testSong()
	d := newTestService(t, song, 360, 20)
	ctx := context.Background()
	userID := uuid.New()

	created, _, err := d.svc.Enqueue(ctx, userID, song.ID, domain.AnalysisModeClean, false, domain.LocaleEN, validWAVReader())
	require.NoError(t, err)
	_, _, err = d.svc.Cancel(ctx, created.ID, userID)
	require.NoError(t, err)

	_, _, err = d.svc.Cancel(ctx, created.ID, userID)
	require.ErrorIs(t, err, domain.ErrAnalysisNotQueued)
}

func TestCancel_WaitingForReference_Succeeds(t *testing.T) {
	song := waitingSong()
	d := newTestService(t, song, 360, 20)
	ctx := context.Background()
	userID := uuid.New()

	created, _, err := d.svc.Enqueue(ctx, userID, song.ID, domain.AnalysisModeClean, false, domain.LocaleEN, validWAVReader())
	require.NoError(t, err)
	require.Equal(t, domain.AnalysisStatusWaitingForReference, created.Status)
	require.Nil(t, created.QueueStreamID, "never published to analyses:run, so nothing to remove")

	canceled, _, err := d.svc.Cancel(ctx, created.ID, userID)
	require.NoError(t, err)
	require.Equal(t, domain.AnalysisStatusCanceled, canceled.Status)
	require.Empty(t, d.queue.removed, "a waiting_for_reference job was never published, so Remove is never called")
}

func TestCancel_WrongOwner_ReturnsErrNotFound(t *testing.T) {
	song := testSong()
	d := newTestService(t, song, 360, 20)
	ctx := context.Background()

	created, _, err := d.svc.Enqueue(ctx, uuid.New(), song.ID, domain.AnalysisModeClean, false, domain.LocaleEN, validWAVReader())
	require.NoError(t, err)

	_, _, err = d.svc.Cancel(ctx, created.ID, uuid.New())
	require.ErrorIs(t, err, domain.ErrNotFound)
}

func TestCancel_ShiftsRemainingPositions(t *testing.T) {
	song := testSong()
	d := newTestService(t, song, 360, 20)
	ctx := context.Background()
	userID := uuid.New()

	first, _, err := d.svc.Enqueue(ctx, userID, song.ID, domain.AnalysisModeClean, false, domain.LocaleEN, validWAVReader())
	require.NoError(t, err)
	second, _, err := d.svc.Enqueue(ctx, userID, song.ID, domain.AnalysisModeClean, false, domain.LocaleEN, validWAVReader())
	require.NoError(t, err)
	third, _, err := d.svc.Enqueue(ctx, userID, song.ID, domain.AnalysisModeClean, false, domain.LocaleEN, validWAVReader())
	require.NoError(t, err)

	_, positions, err := d.svc.Cancel(ctx, first.ID, userID)
	require.NoError(t, err)

	_, stillQueued := positions[first.ID]
	require.False(t, stillQueued, "a canceled job must not appear in the changed-position set")
	require.Equal(t, 1, positions[second.ID])
	require.Equal(t, 2, positions[third.ID])
}

func TestCancel_QueueRemoveFails_CancelStillSucceeds(t *testing.T) {
	song := testSong()
	d := newTestService(t, song, 360, 20)
	ctx := context.Background()
	userID := uuid.New()

	created, _, err := d.svc.Enqueue(ctx, userID, song.ID, domain.AnalysisModeClean, false, domain.LocaleEN, validWAVReader())
	require.NoError(t, err)
	d.queue.removeErr = errBoom

	canceled, _, err := d.svc.Cancel(ctx, created.ID, userID)
	require.NoError(t, err, "Postgres status is authoritative; a Redis cleanup failure must not fail the cancel")
	require.Equal(t, domain.AnalysisStatusCanceled, canceled.Status)
}
