// Package progress implements reading back a user's FR-35 progress-chart
// points. It only reads: every write to progress_snapshots comes from the
// E3 worker (worker/src/vocalcoach/repositories/postgres.py), not this API.
package progress

import (
	"context"

	"github.com/google/uuid"

	"ai-vocal-coach/api/internal/domain"
)

// Repository lists a user's progress points (internal/repository/postgres.ProgressRepository).
type Repository interface {
	ListByUser(ctx context.Context, userID uuid.UUID) ([]domain.ProgressPoint, error)
}
