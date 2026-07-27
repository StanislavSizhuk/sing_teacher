package song_test

import (
	"context"
	"strings"
	"testing"

	"github.com/stretchr/testify/require"

	"ai-vocal-coach/api/internal/domain"
	"ai-vocal-coach/api/internal/service/song"
	"ai-vocal-coach/api/internal/storage"
)

const testMaxUploadBytes = 15 * 1024 * 1024

func newTestService(t *testing.T, repo *fakeRepository, processor *fakeAudioProcessor, yt *fakeYouTubeClient, maxAudioSeconds int, youtubeEnabled bool) *song.Service {
	t.Helper()
	files, err := storage.NewFileStore(t.TempDir())
	require.NoError(t, err)
	return song.NewService(repo, processor, files, yt, testMaxUploadBytes, maxAudioSeconds, youtubeEnabled)
}

func validWAVReader() *strings.Reader {
	return strings.NewReader("RIFF____WAVEfmt \x00")
}

func TestAddFromUpload_NewFile_Created(t *testing.T) {
	repo := newFakeRepository()
	processor := &fakeAudioProcessor{seconds: 180}
	svc := newTestService(t, repo, processor, nil, 360, false)

	got, reused, err := svc.AddFromUpload(context.Background(), "My Song", "Artist", validWAVReader())
	require.NoError(t, err)
	require.False(t, reused)
	require.Equal(t, "My Song", got.Title)
	require.NotNil(t, got.Artist)
	require.Equal(t, "Artist", *got.Artist)
	require.Equal(t, 180, got.DurationSec)
	require.Equal(t, 1, processor.transcodeCalls)
}

func TestAddFromUpload_UnsupportedFormat_Rejected(t *testing.T) {
	repo := newFakeRepository()
	processor := &fakeAudioProcessor{seconds: 10}
	svc := newTestService(t, repo, processor, nil, 360, false)

	_, _, err := svc.AddFromUpload(context.Background(), "Song", "", strings.NewReader("not audio at all"))
	require.ErrorIs(t, err, domain.ErrUnsupportedAudioFormat)
	require.Equal(t, 0, processor.transcodeCalls, "an unsupported format must never reach transcode")
}

func TestAddFromUpload_TooLong_Rejected(t *testing.T) {
	repo := newFakeRepository()
	processor := &fakeAudioProcessor{seconds: 400}
	svc := newTestService(t, repo, processor, nil, 360, false)

	_, _, err := svc.AddFromUpload(context.Background(), "Song", "", validWAVReader())
	require.ErrorIs(t, err, domain.ErrAudioTooLong)
	require.Equal(t, 0, processor.transcodeCalls, "a too-long file must never reach transcode")
}

func TestAddFromUpload_ProbeError_Propagated(t *testing.T) {
	repo := newFakeRepository()
	processor := &fakeAudioProcessor{probeErr: domain.ErrUnsupportedAudioFormat}
	svc := newTestService(t, repo, processor, nil, 360, false)

	_, _, err := svc.AddFromUpload(context.Background(), "Song", "", validWAVReader())
	require.ErrorIs(t, err, domain.ErrUnsupportedAudioFormat)
}

func TestAddFromUpload_DuplicateContent_Reused(t *testing.T) {
	repo := newFakeRepository()
	// Both uploads transcode to byte-identical canonical audio, so their
	// content hash matches -- the real-world case of the same song
	// uploaded twice (spec 6.6 dedup, FR-13).
	fixedContent := []byte("identical canonical bytes")
	processor := &fakeAudioProcessor{seconds: 120, transcodeBytes: fixedContent}
	svc := newTestService(t, repo, processor, nil, 360, false)
	ctx := context.Background()

	first, reused, err := svc.AddFromUpload(ctx, "First Title", "", validWAVReader())
	require.NoError(t, err)
	require.False(t, reused)

	second, reused, err := svc.AddFromUpload(ctx, "A Different Title", "", validWAVReader())
	require.NoError(t, err)
	require.True(t, reused)
	require.Equal(t, first.ID, second.ID)
	require.Equal(t, "First Title", second.Title, "the original song's metadata wins, not the duplicate submission's")
}

func TestAddFromUpload_TooLarge_Rejected(t *testing.T) {
	files, err := storage.NewFileStore(t.TempDir())
	require.NoError(t, err)
	svc := song.NewService(newFakeRepository(), &fakeAudioProcessor{}, files, nil, 4, 360, false)

	_, _, err = svc.AddFromUpload(context.Background(), "Song", "", strings.NewReader("this is way more than four bytes"))
	require.ErrorIs(t, err, domain.ErrAudioTooLarge)
}

func TestAddFromUpload_TranscodeError_CleansUpTempFile(t *testing.T) {
	dir := t.TempDir()
	files, err := storage.NewFileStore(dir)
	require.NoError(t, err)
	processor := &fakeAudioProcessor{seconds: 10, transcodeErr: errBoom}
	svc := song.NewService(newFakeRepository(), processor, files, nil, testMaxUploadBytes, 360, false)

	_, _, err = svc.AddFromUpload(context.Background(), "Song", "", validWAVReader())
	require.Error(t, err)

	entries, err := readDirNames(dir)
	require.NoError(t, err)
	require.Empty(t, entries, "the raw scratch file must be cleaned up even when transcode fails")
}
