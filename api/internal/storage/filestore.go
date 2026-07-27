// Package storage persists audio files under a server-generated path,
// never a user-supplied filename (spec 11.3), and provides the interim
// time-based deletion safety net for spec 7.2/FR-43 until stage E3's worker
// ties deletion to actual processing completion.
package storage

import (
	"fmt"
	"io"
	"os"
	"path/filepath"
	"time"

	"github.com/google/uuid"

	"ai-vocal-coach/api/internal/domain"
)

// FileStore reads and writes audio under one directory, shared with the
// (future, E3) ML worker via a Docker volume (spec 5.2 "audio-tmp").
type FileStore struct {
	dir string
}

// NewFileStore builds a FileStore rooted at dir, creating it if missing.
func NewFileStore(dir string) (*FileStore, error) {
	if err := os.MkdirAll(dir, 0o750); err != nil {
		return nil, fmt.Errorf("create audio storage dir: %w", err)
	}
	return &FileStore{dir: dir}, nil
}

// WriteTemp copies r into a new scratch file inside the store, refusing
// anything past maxBytes, and returns its path. Callers own its lifetime and
// must Remove it once done (typically via defer, right after validating and
// transcoding it into a canonical file with PathFor).
func (s *FileStore) WriteTemp(r io.Reader, maxBytes int64) (path string, err error) {
	f, err := os.CreateTemp(s.dir, "upload-*.raw")
	if err != nil {
		return "", fmt.Errorf("create temp file: %w", err)
	}
	defer func() { _ = f.Close() }()

	n, err := io.Copy(f, io.LimitReader(r, maxBytes+1))
	if err != nil {
		_ = os.Remove(f.Name())
		return "", fmt.Errorf("write temp file: %w", err)
	}
	if n > maxBytes {
		_ = os.Remove(f.Name())
		return "", domain.ErrAudioTooLarge
	}
	return f.Name(), nil
}

// PathFor returns the canonical on-disk path for a server-generated id
// (never a user-supplied filename, spec 11.3). prefix distinguishes what the
// id belongs to (e.g. "song", "analysis") so both can share one directory.
func (s *FileStore) PathFor(prefix string, id uuid.UUID) string {
	return filepath.Join(s.dir, prefix+"-"+id.String()+".wav")
}

// Remove deletes path. A file that is already gone is not an error: callers
// use this both for definite cleanup and for best-effort rollback.
func (s *FileStore) Remove(path string) error {
	if err := os.Remove(path); err != nil && !os.IsNotExist(err) {
		return fmt.Errorf("remove file %s: %w", path, err)
	}
	return nil
}

// Sweep deletes every file directly under the store older than maxAge and
// reports how many were removed (spec 7.2, FR-43: audio deleted no later
// than 5 minutes after processing ends). This stage has no worker yet to
// signal "processing ended", so age-since-write is used as an interim,
// conservative stand-in.
func (s *FileStore) Sweep(maxAge time.Duration) (removed int, err error) {
	entries, err := os.ReadDir(s.dir)
	if err != nil {
		return 0, fmt.Errorf("read audio storage dir: %w", err)
	}

	cutoff := time.Now().Add(-maxAge)
	for _, entry := range entries {
		if entry.IsDir() {
			continue
		}
		info, err := entry.Info()
		if err != nil {
			continue // file raced out from under us between ReadDir and Info; nothing to sweep
		}
		if info.ModTime().Before(cutoff) {
			if err := os.Remove(filepath.Join(s.dir, entry.Name())); err == nil {
				removed++
			}
		}
	}
	return removed, nil
}
