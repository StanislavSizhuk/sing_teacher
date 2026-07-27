// Package song implements song ingestion: uploading a file or importing
// from YouTube, both converging on the same validate -> canonicalize ->
// deduplicate pipeline (spec 6.6, FR-10..14). Every external dependency is
// declared here as an interface, implemented in internal/repository,
// internal/media, internal/storage and internal/youtube.
package song

import (
	"context"
	"io"

	"github.com/google/uuid"

	"ai-vocal-coach/api/internal/domain"
	"ai-vocal-coach/api/internal/youtube"
)

// Repository persists and deduplicates the song catalog.
type Repository interface {
	// GetOrCreate inserts song unless its ContentHash already exists, in
	// which case the existing row is returned instead (spec 6.6, FR-13).
	GetOrCreate(ctx context.Context, song *domain.Song) (result *domain.Song, created bool, err error)
	GetByID(ctx context.Context, id uuid.UUID) (*domain.Song, error)
}

// AudioProcessor validates and canonicalizes audio (internal/media.Processor).
type AudioProcessor interface {
	Probe(ctx context.Context, path string) (seconds float64, err error)
	Transcode(ctx context.Context, srcPath, dstPath string) error
}

// FileStore persists canonical audio under a server-generated path
// (internal/storage.FileStore).
type FileStore interface {
	WriteTemp(r io.Reader, maxBytes int64) (path string, err error)
	PathFor(prefix string, id uuid.UUID) string
	Remove(path string) error
}

// YouTubeClient fetches metadata and downloads audio (internal/youtube.Client).
type YouTubeClient interface {
	// Metadata fetches id/title/duration without downloading, so the
	// duration limit can be enforced before spending any bandwidth (FR-12).
	Metadata(ctx context.Context, videoURL string) (youtube.VideoInfo, error)
	Download(ctx context.Context, videoURL, destDir string) (path string, err error)
}
