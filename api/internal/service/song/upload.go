package song

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"os"

	"github.com/google/uuid"

	"ai-vocal-coach/api/internal/domain"
	"ai-vocal-coach/api/internal/media"
)

// AddFromUpload validates, canonicalizes and stores an uploaded song file,
// deduplicating by the resulting audio's content hash (FR-10, FR-13, spec
// 11.3: magic bytes checked, size/duration capped, re-encoded to canonical
// WAV before anything else touches it).
func (s *Service) AddFromUpload(ctx context.Context, title, artist string, file io.Reader) (result *domain.Song, reused bool, err error) {
	rawPath, err := s.files.WriteTemp(file, s.maxUploadBytes)
	if err != nil {
		return nil, false, err
	}
	defer func() { _ = s.files.Remove(rawPath) }()

	if _, ok, err := media.SniffFile(rawPath); err != nil {
		return nil, false, err
	} else if !ok {
		return nil, false, domain.ErrUnsupportedAudioFormat
	}

	seconds, err := s.processor.Probe(ctx, rawPath)
	if err != nil {
		return nil, false, err
	}
	if int(seconds) > s.maxAudioSeconds {
		return nil, false, domain.ErrAudioTooLong
	}

	songID := uuid.New()
	canonicalPath := s.files.PathFor(filePrefix, songID)
	if err := s.processor.Transcode(ctx, rawPath, canonicalPath); err != nil {
		return nil, false, fmt.Errorf("transcode uploaded song: %w", err)
	}

	hash, err := hashFile(canonicalPath)
	if err != nil {
		_ = s.files.Remove(canonicalPath)
		return nil, false, fmt.Errorf("hash canonical audio: %w", err)
	}

	candidate := &domain.Song{
		ID:          songID,
		SourceType:  domain.SongSourceUpload,
		ContentHash: hash,
		Title:       title,
		DurationSec: int(seconds),
	}
	if artist != "" {
		candidate.Artist = &artist
	}

	saved, created, err := s.songs.GetOrCreate(ctx, candidate)
	if err != nil {
		_ = s.files.Remove(canonicalPath)
		return nil, false, fmt.Errorf("save song: %w", err)
	}
	if !created {
		// Another song already holds this content hash: the file we just
		// canonicalized is a throwaway duplicate of audio that (if its TTL
		// has not yet swept it) already lives under the existing song's id.
		_ = s.files.Remove(canonicalPath)
	}
	return saved, !created, nil
}

// hashFile returns the hex sha256 of path's contents -- the dedup key for
// uploaded songs (spec 6.6: "sha256 нормалізованого аудіо").
func hashFile(path string) (string, error) {
	f, err := os.Open(path) // #nosec G304 -- path is always our own storage-managed path, never user input
	if err != nil {
		return "", fmt.Errorf("open file for hashing: %w", err)
	}
	defer func() { _ = f.Close() }()

	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		return "", fmt.Errorf("hash file: %w", err)
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}
