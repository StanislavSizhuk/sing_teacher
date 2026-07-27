package song

import (
	"context"

	"github.com/google/uuid"

	"ai-vocal-coach/api/internal/domain"
)

// filePrefix names canonical song files on disk (song-<id>.wav), never a
// user-supplied filename (spec 11.3).
const filePrefix = "song"

// Service implements song ingestion.
type Service struct {
	songs           Repository
	processor       AudioProcessor
	files           FileStore
	youtubeClient   YouTubeClient
	maxUploadBytes  int64
	maxAudioSeconds int
	youtubeEnabled  bool
}

// NewService wires the song service to its dependencies. maxUploadBytes and
// maxAudioSeconds come from config.Limits; youtubeEnabled from
// config.Features.YouTubeImport (spec 11.4: off by default in production).
func NewService(
	songs Repository,
	processor AudioProcessor,
	files FileStore,
	youtubeClient YouTubeClient,
	maxUploadBytes int64,
	maxAudioSeconds int,
	youtubeEnabled bool,
) *Service {
	return &Service{
		songs:           songs,
		processor:       processor,
		files:           files,
		youtubeClient:   youtubeClient,
		maxUploadBytes:  maxUploadBytes,
		maxAudioSeconds: maxAudioSeconds,
		youtubeEnabled:  youtubeEnabled,
	}
}

// GetByID returns the song's current preparation status (FR-14). Every
// song's VocalStemProcessed is false until the E3 worker exists.
func (s *Service) GetByID(ctx context.Context, id uuid.UUID) (*domain.Song, error) {
	return s.songs.GetByID(ctx, id)
}
