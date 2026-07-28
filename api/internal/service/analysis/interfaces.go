// Package analysis implements putting a recording in the analysis queue,
// tracking its position, and canceling it while queued (spec 10, FR-22..25).
// Every external dependency is declared here as an interface, implemented
// in internal/repository, internal/media, internal/storage and internal/queue.
package analysis

import (
	"context"
	"io"
	"time"

	"github.com/google/uuid"

	"ai-vocal-coach/api/internal/domain"
)

// Repository persists analysis jobs and their queue ordering.
type Repository interface {
	Create(ctx context.Context, a *domain.Analysis) error
	SetQueueStreamID(ctx context.Context, id uuid.UUID, streamEntryID string) error
	GetByID(ctx context.Context, id, userID uuid.UUID) (*domain.Analysis, error)
	Cancel(ctx context.Context, id, userID uuid.UUID) (*domain.Analysis, error)
	// Retry moves a failed analysis back to queued, at the back of the FIFO
	// order, without touching its stored recording (FR-26).
	Retry(ctx context.Context, id, userID uuid.UUID) (*domain.Analysis, error)
	// RecalculatePositions reassigns FIFO queue_position to every queued
	// analysis and returns only the ids whose position changed -- the set
	// the caller needs to push over WebSocket (spec 10).
	RecalculatePositions(ctx context.Context) (map[uuid.UUID]int, error)
}

// SongRepository is the narrow slice of song lookups this package needs: an
// analysis can only be queued against a song that exists.
type SongRepository interface {
	GetByID(ctx context.Context, id uuid.UUID) (*domain.Song, error)
}

// AudioProcessor validates the recording (internal/media.Processor).
type AudioProcessor interface {
	Probe(ctx context.Context, path string) (seconds float64, err error)
	Transcode(ctx context.Context, srcPath, dstPath string) error
}

// FileStore persists the canonical recording under a server-generated path
// (internal/storage.FileStore).
type FileStore interface {
	WriteTemp(r io.Reader, maxBytes int64) (path string, err error)
	PathFor(prefix string, id uuid.UUID) string
	Remove(path string) error
}

// RateLimiter enforces USER_ANALYSES_PER_HOUR
// (internal/repository/redisrepo.AnalysisRateLimiter).
type RateLimiter interface {
	Allow(ctx context.Context, userID uuid.UUID) (allowed bool, retryAfter time.Duration, err error)
}

// QueueProducer publishes and removes jobs on the Redis Streams queue
// (internal/queue.Producer).
type QueueProducer interface {
	Length(ctx context.Context) (int64, error)
	Enqueue(ctx context.Context, analysisID uuid.UUID) (streamEntryID string, err error)
	Remove(ctx context.Context, streamEntryID string) error
}
