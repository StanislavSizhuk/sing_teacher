package song_test

import (
	"context"
	"os"
	"path/filepath"
	"time"

	"github.com/google/uuid"

	"ai-vocal-coach/api/internal/domain"
	"ai-vocal-coach/api/internal/youtube"
)

// --- fakeRepository ----------------------------------------------------

type fakeRepository struct {
	byID          map[uuid.UUID]*domain.Song
	byContentHash map[string]uuid.UUID
}

func newFakeRepository() *fakeRepository {
	return &fakeRepository{byID: map[uuid.UUID]*domain.Song{}, byContentHash: map[string]uuid.UUID{}}
}

func cloneSong(s *domain.Song) *domain.Song {
	cp := *s
	return &cp
}

func (f *fakeRepository) GetOrCreate(_ context.Context, s *domain.Song) (*domain.Song, bool, error) {
	if id, exists := f.byContentHash[s.ContentHash]; exists {
		return cloneSong(f.byID[id]), false, nil
	}
	cp := cloneSong(s)
	cp.CreatedAt = time.Now()
	f.byID[cp.ID] = cp
	f.byContentHash[cp.ContentHash] = cp.ID
	return cloneSong(cp), true, nil
}

func (f *fakeRepository) GetByID(_ context.Context, id uuid.UUID) (*domain.Song, error) {
	s, ok := f.byID[id]
	if !ok {
		return nil, domain.ErrNotFound
	}
	return cloneSong(s), nil
}

// --- fakeAudioProcessor --------------------------------------------------

// fakeAudioProcessor stands in for ffmpeg/ffprobe: Transcode writes real
// bytes to dst (fixed by default) so the service's own hashFile step -- a
// real sha256 over a real file -- has something genuine to hash.
type fakeAudioProcessor struct {
	seconds        float64
	probeErr       error
	transcodeErr   error
	transcodeBytes []byte
	transcodeCalls int
}

func (f *fakeAudioProcessor) Probe(_ context.Context, _ string) (float64, error) {
	if f.probeErr != nil {
		return 0, f.probeErr
	}
	return f.seconds, nil
}

func (f *fakeAudioProcessor) Transcode(_ context.Context, _, dst string) error {
	f.transcodeCalls++
	if f.transcodeErr != nil {
		return f.transcodeErr
	}
	content := f.transcodeBytes
	if content == nil {
		content = []byte("canonical audio bytes")
	}
	return os.WriteFile(dst, content, 0o600)
}

// --- fakeYouTubeClient -----------------------------------------------------

type fakeYouTubeClient struct {
	info          youtube.VideoInfo
	metadataErr   error
	downloadBytes []byte
	downloadErr   error
	metadataCalls int
	downloadCalls int
}

func (f *fakeYouTubeClient) Metadata(_ context.Context, _ string) (youtube.VideoInfo, error) {
	f.metadataCalls++
	if f.metadataErr != nil {
		return youtube.VideoInfo{}, f.metadataErr
	}
	return f.info, nil
}

func (f *fakeYouTubeClient) Download(_ context.Context, _, destDir string) (string, error) {
	f.downloadCalls++
	if f.downloadErr != nil {
		return "", f.downloadErr
	}
	content := f.downloadBytes
	if content == nil {
		content = append([]byte("RIFF____WAVEfmt "), 0) // valid WAV magic bytes by default
	}
	path := filepath.Join(destDir, "audio.wav")
	if err := os.WriteFile(path, content, 0o600); err != nil {
		return "", err
	}
	return path, nil
}
