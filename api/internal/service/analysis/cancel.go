package analysis

import (
	"context"
	"fmt"

	"github.com/google/uuid"

	"ai-vocal-coach/api/internal/domain"
)

// Cancel moves a queued analysis to canceled and returns the updated
// positions of every job that shifted as a result (FR-25). Postgres's
// status column is the source of truth: removing the Redis Streams entry is
// best-effort tidying, not something a failure here should undo the cancel
// for -- a future worker double-checks status before processing regardless
// (spec 10.1).
func (s *Service) Cancel(ctx context.Context, id, userID uuid.UUID) (a *domain.Analysis, positions map[uuid.UUID]int, err error) {
	canceled, err := s.analyses.Cancel(ctx, id, userID)
	if err != nil {
		return nil, nil, err
	}

	if canceled.QueueStreamID != nil {
		_ = s.queue.Remove(ctx, *canceled.QueueStreamID)
	}

	positions, err = s.analyses.RecalculatePositions(ctx)
	if err != nil {
		return nil, nil, fmt.Errorf("recalculate queue positions after cancel: %w", err)
	}
	return canceled, positions, nil
}
