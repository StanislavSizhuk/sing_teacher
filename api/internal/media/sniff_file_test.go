package media_test

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/stretchr/testify/require"

	"ai-vocal-coach/api/internal/media"
)

func TestSniffFile_ValidWav_Detected(t *testing.T) {
	path := filepath.Join(t.TempDir(), "in.raw")
	require.NoError(t, os.WriteFile(path, append([]byte("RIFF____WAVEfmt "), 0), 0o600))

	format, ok, err := media.SniffFile(path)
	require.NoError(t, err)
	require.True(t, ok)
	require.Equal(t, media.FormatWAV, format)
}

func TestSniffFile_UnsupportedContent_NotOK(t *testing.T) {
	path := filepath.Join(t.TempDir(), "in.raw")
	require.NoError(t, os.WriteFile(path, []byte("not audio at all"), 0o600))

	_, ok, err := media.SniffFile(path)
	require.NoError(t, err)
	require.False(t, ok)
}

func TestSniffFile_EmptyFile_NotOK(t *testing.T) {
	path := filepath.Join(t.TempDir(), "empty.raw")
	require.NoError(t, os.WriteFile(path, nil, 0o600))

	_, ok, err := media.SniffFile(path)
	require.NoError(t, err)
	require.False(t, ok)
}

func TestSniffFile_MissingFile_Errors(t *testing.T) {
	_, _, err := media.SniffFile(filepath.Join(t.TempDir(), "does-not-exist.raw"))
	require.Error(t, err)
}
