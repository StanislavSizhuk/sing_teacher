package song

import (
	"context"
	"fmt"

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
	prepQueue       PrepQueueProducer
	maxUploadBytes  int64
	maxAudioSeconds int
	prepQueueMaxLen int64
	youtubeEnabled  bool
}

// NewService wires the song service to its dependencies. maxUploadBytes and
// maxAudioSeconds come from config.Limits; prepQueueMaxLen is
// config.Limits.QueueMaxLength applied to songs:prep (spec 10.1: the same
// 20-entry cap independently bounds each stream); youtubeEnabled from
// config.Features.YouTubeImport (spec 11.4: off by default in production).
func NewService(
	songs Repository,
	processor AudioProcessor,
	files FileStore,
	youtubeClient YouTubeClient,
	prepQueue PrepQueueProducer,
	maxUploadBytes int64,
	maxAudioSeconds int,
	prepQueueMaxLen int,
	youtubeEnabled bool,
) *Service {
	return &Service{
		songs:           songs,
		processor:       processor,
		files:           files,
		youtubeClient:   youtubeClient,
		prepQueue:       prepQueue,
		maxUploadBytes:  maxUploadBytes,
		maxAudioSeconds: maxAudioSeconds,
		prepQueueMaxLen: int64(prepQueueMaxLen),
		youtubeEnabled:  youtubeEnabled,
	}
}

// GetByID returns the song's current preparation status (FR-14): PrepStatus/
// PrepStage/PrepErrorCode reflect the cold path's live progress.
func (s *Service) GetByID(ctx context.Context, id uuid.UUID) (*domain.Song, error) {
	return s.songs.GetByID(ctx, id)
}

// enqueuePrep puts a freshly created song's cold path onto songs:prep (FR-15:
// "POST /songs ставить підготовку в чергу одразу"). Only ever called for a
// genuinely new row -- GetOrCreate's dedup means a reused song either
// already has (or is already getting) its reference prepared, so queuing it
// again would waste a whole Demucs/Whisper run on work already in flight or
// done. On a full queue, it rolls back the just-created row and its
// canonical file so the request fails clean rather than leaving an orphaned
// song stuck in prep_status='pending' forever.
func (s *Service) enqueuePrep(ctx context.Context, songID uuid.UUID, canonicalPath string) error {
	// No per-song queue-position tracking on songs:prep (spec 10 only
	// requires that for analyses:run, FR-22/23), so the entry id itself is
	// discarded once admission succeeds.
	_, ok, err := s.prepQueue.EnqueueIfUnderLimit(ctx, songID, s.prepQueueMaxLen)
	if err != nil {
		return fmt.Errorf("enqueue song prep: %w", err)
	}
	if !ok {
		_ = s.files.Remove(canonicalPath)
		if delErr := s.songs.Delete(ctx, songID); delErr != nil {
			return fmt.Errorf("roll back song after prep queue-full: %w", delErr)
		}
		return domain.ErrQueueFull
	}
	return nil
}
