package progress

import (
	"context"

	"github.com/google/uuid"

	"ai-vocal-coach/api/internal/domain"
)

// Service reads back the caller's own progress-chart points (FR-35).
type Service struct {
	points Repository
}

// NewService wires the progress service to its repository.
func NewService(points Repository) *Service {
	return &Service{points: points}
}

// ListByUser returns userID's progress points, oldest first (spec 11: a
// user only ever sees their own data -- there is no cross-user query here
// to get wrong).
func (s *Service) ListByUser(ctx context.Context, userID uuid.UUID) ([]domain.ProgressPoint, error) {
	return s.points.ListByUser(ctx, userID)
}
