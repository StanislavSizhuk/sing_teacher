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
func (s *Service) Retry(ctx context.Context, id, userID uuid.UUID) (a *domain.Analysis, positions map[uuid.UUID]int, err error) {
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
