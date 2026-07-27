package storage_test

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/stretchr/testify/require"

	"ai-vocal-coach/api/internal/domain"
	"ai-vocal-coach/api/internal/storage"
)

func TestNewFileStore_CreatesDirIfMissing(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "nested", "audio-tmp")
	_, err := storage.NewFileStore(dir)
	require.NoError(t, err)
	info, err := os.Stat(dir)
	require.NoError(t, err)
	require.True(t, info.IsDir())
}

func TestFileStore_WriteTemp_WritesContent(t *testing.T) {
	s, err := storage.NewFileStore(t.TempDir())
	require.NoError(t, err)

	path, err := s.WriteTemp(strings.NewReader("hello audio bytes"), 1024)
	require.NoError(t, err)
	t.Cleanup(func() { _ = s.Remove(path) })

	got, err := os.ReadFile(path) // #nosec G304 -- path is this test's own WriteTemp() return value
	require.NoError(t, err)
	require.Equal(t, "hello audio bytes", string(got))
}

func TestFileStore_WriteTemp_ExceedsMax_ReturnsErrAudioTooLarge(t *testing.T) {
	dir := t.TempDir()
	s, err := storage.NewFileStore(dir)
	require.NoError(t, err)

	_, err = s.WriteTemp(strings.NewReader("this is definitely more than five bytes"), 5)
	require.ErrorIs(t, err, domain.ErrAudioTooLarge)

	entries, err := os.ReadDir(dir)
	require.NoError(t, err)
	require.Empty(t, entries, "the oversized temp file must be cleaned up, not left behind")
}

func TestFileStore_PathFor_DerivedFromServerGeneratedID(t *testing.T) {
	s, err := storage.NewFileStore(t.TempDir())
	require.NoError(t, err)

	id := uuid.New()
	path := s.PathFor("song", id)
	require.Equal(t, "song-"+id.String()+".wav", filepath.Base(path))
}

func TestFileStore_Remove_MissingFile_NoError(t *testing.T) {
	s, err := storage.NewFileStore(t.TempDir())
	require.NoError(t, err)
	require.NoError(t, s.Remove("/no/such/file.wav"))
}

func TestFileStore_Sweep_RemovesOnlyStaleFiles(t *testing.T) {
	dir := t.TempDir()
	s, err := storage.NewFileStore(dir)
	require.NoError(t, err)

	stalePath := filepath.Join(dir, "stale.wav")
	freshPath := filepath.Join(dir, "fresh.wav")
	require.NoError(t, os.WriteFile(stalePath, []byte("old"), 0o600))
	require.NoError(t, os.WriteFile(freshPath, []byte("new"), 0o600))

	old := time.Now().Add(-10 * time.Minute)
	require.NoError(t, os.Chtimes(stalePath, old, old))

	removed, err := s.Sweep(5 * time.Minute)
	require.NoError(t, err)
	require.Equal(t, 1, removed)

	_, err = os.Stat(stalePath)
	require.True(t, os.IsNotExist(err), "stale file must be removed")
	_, err = os.Stat(freshPath)
	require.NoError(t, err, "fresh file must survive")
}
