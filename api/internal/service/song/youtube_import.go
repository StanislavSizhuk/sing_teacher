package song

import (
	"context"
	"fmt"
	"os"

	"github.com/google/uuid"

	"ai-vocal-coach/api/internal/domain"
	"ai-vocal-coach/api/internal/media"
	"ai-vocal-coach/api/internal/youtube"
)

// AddFromYouTube imports a song by YouTube link (FR-11). It is a no-op past
// the URL/feature check when the video's content hash is already known
// (spec 6.6 cache key = youtube_video_id): metadata alone is enough to
// detect that, so a repeat submission never re-downloads.
func (s *Service) AddFromYouTube(ctx context.Context, rawURL, titleOverride string) (result *domain.Song, reused bool, err error) {
	if !s.youtubeEnabled {
		return nil, false, domain.ErrYouTubeImportDisabled
	}
	videoURL, err := youtube.ValidateURL(rawURL)
	if err != nil {
		return nil, false, err
	}

	info, err := s.youtubeClient.Metadata(ctx, videoURL)
	if err != nil {
		return nil, false, err
	}
	// Duration is checked before any download (FR-12), which this cache-key
	// short-circuit makes essentially free: Metadata is the only network
	// call needed to find out this song was already imported.
	if int(info.DurationSeconds) > s.maxAudioSeconds {
		return nil, false, domain.ErrYouTubeVideoTooLong
	}

	title := titleOverride
	if title == "" {
		title = info.Title
	}
	candidate := &domain.Song{
		ID:          uuid.New(),
		SourceType:  domain.SongSourceYouTube,
		SourceURL:   &videoURL,
		ContentHash: "youtube:" + info.ID,
		Title:       title,
		DurationSec: int(info.DurationSeconds),
	}

	saved, created, err := s.songs.GetOrCreate(ctx, candidate)
	if err != nil {
		return nil, false, fmt.Errorf("save youtube song: %w", err)
	}
	if !created {
		// Its cold path is already queued, running, or done -- never re-enqueue it.
		return saved, true, nil
	}

	canonicalPath := s.files.PathFor(filePrefix, saved.ID)
	if err := s.downloadAndStore(ctx, videoURL, saved.ID); err != nil {
		// The song row now exists without its canonical audio file, so it
		// can never leave prep_status='pending' -- known, documented gap
		// (see ARCHITECTURE.md) rather than compensating-transaction
		// machinery for a network call that already failed.
		return nil, false, err
	}

	if err := s.enqueuePrep(ctx, saved.ID, canonicalPath); err != nil {
		return nil, false, err
	}
	return saved, false, nil
}

// downloadAndStore fetches videoURL's audio into a scratch workspace, then
// runs it through the exact same sniff+transcode pipeline as an uploaded
// file (spec 11.3: every byte reaching canonical storage is sanitized the
// same way, regardless of source).
func (s *Service) downloadAndStore(ctx context.Context, videoURL string, songID uuid.UUID) error {
	tempDir, err := os.MkdirTemp("", "ytdl-*")
	if err != nil {
		return fmt.Errorf("create youtube download workspace: %w", err)
	}
	defer func() { _ = os.RemoveAll(tempDir) }()

	rawPath, err := s.youtubeClient.Download(ctx, videoURL, tempDir)
	if err != nil {
		return err
	}

	if _, ok, err := media.SniffFile(rawPath); err != nil {
		return err
	} else if !ok {
		return domain.ErrUnsupportedAudioFormat
	}

	canonicalPath := s.files.PathFor(filePrefix, songID)
	if err := s.processor.Transcode(ctx, rawPath, canonicalPath); err != nil {
		return fmt.Errorf("transcode youtube audio: %w", err)
	}
	return nil
}
