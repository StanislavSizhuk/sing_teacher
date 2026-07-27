package youtube_test

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"testing"

	"github.com/stretchr/testify/require"

	"ai-vocal-coach/api/internal/youtube"
)

type fakeRunner struct {
	stdout     []byte
	stderr     []byte
	err        error
	gotName    string
	gotArgs    []string
	writeOnRun string // if set, Run also writes an empty file at this path (simulates yt-dlp's download side effect)
}

func (f *fakeRunner) Run(_ context.Context, name string, args []string) ([]byte, []byte, error) {
	f.gotName = name
	f.gotArgs = args
	if f.writeOnRun != "" {
		_ = os.WriteFile(f.writeOnRun, []byte("fake wav bytes"), 0o600)
	}
	return f.stdout, f.stderr, f.err
}

func TestClient_Metadata_ParsesDurationBeforeDownload(t *testing.T) {
	runner := &fakeRunner{stdout: []byte(`{"id":"dQw4w9WgXcQ","title":"Test Song","duration":212.5}`)}
	c := youtube.NewClient(runner, "yt-dlp")

	info, err := c.Metadata(context.Background(), "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
	require.NoError(t, err)
	require.Equal(t, "dQw4w9WgXcQ", info.ID)
	require.Equal(t, "Test Song", info.Title)
	require.InDelta(t, 212.5, info.DurationSeconds, 0.001)

	require.Equal(t, "yt-dlp", runner.gotName)
	require.Contains(t, runner.gotArgs, "--skip-download", "metadata must never trigger a download (FR-12)")
}

func TestClient_Metadata_MissingID_Errors(t *testing.T) {
	runner := &fakeRunner{stdout: []byte(`{"title":"No ID Here","duration":10}`)}
	c := youtube.NewClient(runner, "yt-dlp")

	_, err := c.Metadata(context.Background(), "https://youtu.be/x")
	require.Error(t, err)
}

func TestClient_Metadata_RunnerError_Wrapped(t *testing.T) {
	runner := &fakeRunner{stderr: []byte("Video unavailable"), err: errors.New("exit status 1")}
	c := youtube.NewClient(runner, "yt-dlp")

	_, err := c.Metadata(context.Background(), "https://youtu.be/gone")
	require.Error(t, err)
	require.ErrorContains(t, err, "Video unavailable")
}

func TestClient_Download_ReturnsFixedFilename(t *testing.T) {
	dir := t.TempDir()
	want := filepath.Join(dir, "audio.wav")
	runner := &fakeRunner{writeOnRun: want}
	c := youtube.NewClient(runner, "yt-dlp")

	path, err := c.Download(context.Background(), "https://youtu.be/x", dir)
	require.NoError(t, err)
	require.Equal(t, want, path)
	require.Contains(t, runner.gotArgs, "--audio-format")
	require.Contains(t, runner.gotArgs, "wav")
}

func TestClient_Download_MissingOutputFile_Errors(t *testing.T) {
	dir := t.TempDir()
	runner := &fakeRunner{} // succeeds but never writes the expected file
	c := youtube.NewClient(runner, "yt-dlp")

	_, err := c.Download(context.Background(), "https://youtu.be/x", dir)
	require.Error(t, err)
}

func TestClient_Download_RunnerError_Wrapped(t *testing.T) {
	dir := t.TempDir()
	runner := &fakeRunner{stderr: []byte("network error"), err: errors.New("exit status 1")}
	c := youtube.NewClient(runner, "yt-dlp")

	_, err := c.Download(context.Background(), "https://youtu.be/x", dir)
	require.Error(t, err)
	require.ErrorContains(t, err, "network error")
}

func TestCheckBinary_MissingBinary_Error(t *testing.T) {
	require.Error(t, youtube.CheckBinary("definitely-not-yt-dlp-xyz"))
}
