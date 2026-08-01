package analysis

import (
	"context"
	"fmt"

	"github.com/google/uuid"

	"ai-vocal-coach/api/internal/domain"
)

// Retry restarts a failed analysis without asking the user to re-upload
// anything: the canonical recording on disk and the song reference are
// untouched, only status/error/queue bookkeeping are reset (FR-26). It
// respects the same queue-capacity cap as Enqueue -- a retry still adds a
// fresh entry to the Redis stream -- but not the per-user rate limit, since
// throttling a user's only path to recover a stuck job would defeat the
// point of retry.
//
// Mirrors Enqueue's own admission check against the song's cold path
// (spec 6.2, FR-16/17): an analysis can fail for reasons that have nothing
// to do with its song (a transient ML crash), but it can also fail because
// its song's prep itself failed and every waiting analysis was carried down
// with it (queue/prep_handler.py's fail_waiting_for_reference). Retrying
// that kind blindly back onto analyses:run used to publish a job the
// worker can never serve -- it has no cached reference to read -- and the
// handler crash left the row stuck showing `queued` forever instead of
// surfacing anything to the user. Now: still failed song -> reject outright
// (the caller must restart the song's own prep first, POST
// /songs/{id}/prepare); song not ready yet -> waiting_for_reference, same
// as a fresh Enqueue.
func (s *Service) Retry(ctx context.Context, id, userID uuid.UUID) (a *domain.Analysis, positions map[uuid.UUID]int, err error) {
	existing, err := s.analyses.GetByID(ctx, id, userID)
	if err != nil {
		return nil, nil, err
	}
	song, err := s.songs.GetByID(ctx, existing.SongID)
	if err != nil {
		return nil, nil, err
	}
	if song.PrepStatus == domain.SongPrepFailed {
		return nil, nil, domain.ErrReferencePrepFailed
	}
	if !song.ReadyForAnalysis() {
		retried, err := s.analyses.RetryToWaitingForReference(ctx, id, userID)
		if err != nil {
			return nil, nil, err
		}
		return retried, nil, nil
	}

	queueLen, err := s.queue.Length(ctx)
	if err != nil {
		return nil, nil, fmt.Errorf("check queue length: %w", err)
	}
	if queueLen >= s.queueMaxLength {
		return nil, nil, domain.ErrQueueFull
	}

	retried, err := s.analyses.Retry(ctx, id, userID)
	if err != nil {
		return nil, nil, err
	}

	streamEntryID, err := s.queue.Enqueue(ctx, id)
	if err != nil {
		return nil, nil, fmt.Errorf("re-enqueue analysis: %w", err)
	}
	if err := s.analyses.SetQueueStreamID(ctx, id, streamEntryID); err != nil {
		return nil, nil, fmt.Errorf("record queue stream id: %w", err)
	}
	retried.QueueStreamID = &streamEntryID

	positions, err = s.analyses.RecalculatePositions(ctx)
	if err != nil {
		return nil, nil, fmt.Errorf("recalculate queue positions after retry: %w", err)
	}
	if pos, ok := positions[id]; ok {
		retried.QueuePosition = &pos
	}
	return retried, positions, nil
}
