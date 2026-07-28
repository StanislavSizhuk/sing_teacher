package analysis

import (
	"context"

	"github.com/google/uuid"

	"ai-vocal-coach/api/internal/domain"
)

// filePrefix names canonical recording files on disk (analysis-<id>.wav),
// never a user-supplied filename (spec 11.3).
const filePrefix = "analysis"

// Service implements queuing, canceling, retrying and reading back analysis
// jobs. Retry (FR-26) has no reachable precondition end-to-end until the E3
// worker exists to ever produce a failed analysis, but the logic is built
// and unit-tested now so E3 only has to wire a worker into it.
type Service struct {
	analyses        Repository
	songs           SongRepository
	processor       AudioProcessor
	files           FileStore
	rateLimiter     RateLimiter
	queue           QueueProducer
	maxUploadBytes  int64
	maxAudioSeconds int
	queueMaxLength  int64
}

// NewService wires the analysis service to its dependencies. Limits come
// from config.Limits.
func NewService(
	analyses Repository,
	songs SongRepository,
	processor AudioProcessor,
	files FileStore,
	rateLimiter RateLimiter,
	queueProducer QueueProducer,
	maxUploadBytes int64,
	maxAudioSeconds int,
	queueMaxLength int,
) *Service {
	return &Service{
		analyses:        analyses,
		songs:           songs,
		processor:       processor,
		files:           files,
		rateLimiter:     rateLimiter,
		queue:           queueProducer,
		maxUploadBytes:  maxUploadBytes,
		maxAudioSeconds: maxAudioSeconds,
		queueMaxLength:  int64(queueMaxLength),
	}
}

// GetByID returns the analysis, scoped to its owner (spec 11: a user
// touches only their own resources).
func (s *Service) GetByID(ctx context.Context, id, userID uuid.UUID) (*domain.Analysis, error) {
	return s.analyses.GetByID(ctx, id, userID)
}
