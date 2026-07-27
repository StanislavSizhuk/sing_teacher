package media_test

import (
	"context"
	"errors"
	"testing"

	"github.com/stretchr/testify/require"

	"ai-vocal-coach/api/internal/media"
)

// fakeRunner records the last invocation and returns canned output, so
// Processor can be tested without ffmpeg/ffprobe installed.
type fakeRunner struct {
	stdout  []byte
	stderr  []byte
	err     error
	gotName string
	gotArgs []string
}

func (f *fakeRunner) Run(_ context.Context, name string, args []string) ([]byte, []byte, error) {
	f.gotName = name
	f.gotArgs = args
	return f.stdout, f.stderr, f.err
}

func TestProcessor_Probe_ParsesDurationFromAudioStream(t *testing.T) {
	runner := &fakeRunner{stdout: []byte(`{
		"streams": [{"codec_type": "audio"}],
		"format": {"duration": "123.456000"}
	}`)}
	p := media.NewProcessor(runner, "ffmpeg", "ffprobe")

	seconds, err := p.Probe(context.Background(), "/audio/song-x.raw")
	require.NoError(t, err)
	require.InDelta(t, 123.456, seconds, 0.001)
	require.Equal(t, "ffprobe", runner.gotName)
	require.Contains(t, runner.gotArgs, "/audio/song-x.raw")
}

func TestProcessor_Probe_NoAudioStream_ReturnsErrNoAudioStream(t *testing.T) {
	runner := &fakeRunner{stdout: []byte(`{
		"streams": [{"codec_type": "video"}],
		"format": {"duration": "10.0"}
	}`)}
	p := media.NewProcessor(runner, "ffmpeg", "ffprobe")

	_, err := p.Probe(context.Background(), "/audio/video-only.raw")
	require.ErrorIs(t, err, media.ErrNoAudioStream)
}

func TestProcessor_Probe_RunnerError_Wrapped(t *testing.T) {
	runner := &fakeRunner{stderr: []byte("Invalid data found"), err: errors.New("exit status 1")}
	p := media.NewProcessor(runner, "ffmpeg", "ffprobe")

	_, err := p.Probe(context.Background(), "/audio/corrupt.raw")
	require.Error(t, err)
	require.ErrorContains(t, err, "Invalid data found")
}

func TestProcessor_Transcode_BuildsSafeArgList(t *testing.T) {
	runner := &fakeRunner{}
	p := media.NewProcessor(runner, "ffmpeg", "ffprobe")

	err := p.Transcode(context.Background(), "/audio/in.raw", "/audio/out.wav")
	require.NoError(t, err)
	require.Equal(t, "ffmpeg", runner.gotName)
	require.Contains(t, runner.gotArgs, "-acodec")
	require.Contains(t, runner.gotArgs, "pcm_s16le")
	require.Contains(t, runner.gotArgs, "/audio/in.raw")
	require.Contains(t, runner.gotArgs, "/audio/out.wav")
	require.NotContains(t, runner.gotArgs, "-i /audio/in.raw", "each argument must be a separate list element, never a joined shell-style string")
}

func TestProcessor_Transcode_RunnerError_Wrapped(t *testing.T) {
	runner := &fakeRunner{stderr: []byte("boom"), err: errors.New("exit status 1")}
	p := media.NewProcessor(runner, "ffmpeg", "ffprobe")

	err := p.Transcode(context.Background(), "/audio/in.raw", "/audio/out.wav")
	require.Error(t, err)
	require.ErrorContains(t, err, "boom")
}

func TestCheckBinaries_MissingBinary_Error(t *testing.T) {
	err := media.CheckBinaries("definitely-not-ffmpeg-xyz", "definitely-not-ffprobe-xyz")
	require.Error(t, err)
}
