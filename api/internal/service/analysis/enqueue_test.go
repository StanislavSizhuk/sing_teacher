package analysis_test

import (
	"context"
	"errors"
	"strings"
	"testing"

	"github.com/google/uuid"
	"github.com/stretchr/testify/require"

	"ai-vocal-coach/api/internal/domain"
	"ai-vocal-coach/api/internal/service/analysis"
	"ai-vocal-coach/api/internal/storage"
)

const testMaxUploadBytes = 15 * 1024 * 1024

var errBoom = errors.New("boom")

type testDeps struct {
	analyses  *fakeAnalysisRepository
	songs     *fakeSongRepository
	processor *fakeAudioProcessor
	rate      *fakeRateLimiter
	queue     *fakeQueueProducer
	svc       *analysis.Service
}

func newTestService(t *testing.T, song *domain.Song, maxAudioSeconds, queueMaxLength int) *testDeps {
	t.Helper()
	files, err := storage.NewFileStore(t.TempDir())
	require.NoError(t, err)

	d := &testDeps{
		analyses:  newFakeAnalysisRepository(),
		songs:     newFakeSongRepository(song),
		processor: &fakeAudioProcessor{seconds: 60},
		rate:      newAllowingRateLimiter(),
		queue:     &fakeQueueProducer{},
	}
	d.svc = analysis.NewService(d.analyses, d.songs, d.processor, files, d.rate, d.queue,
		testMaxUploadBytes, maxAudioSeconds, queueMaxLength)
	return d
}

func testSong() *domain.Song {
	return &domain.Song{ID: uuid.New(), SourceType: domain.SongSourceUpload, ContentHash: "h", Title: "T", DurationSec: 200}
}

func validWAVReader() *strings.Reader {
	return strings.NewReader("RIFF____WAVEfmt \x00")
}

func TestEnqueue_Success_ReturnsAnalysisAndPosition(t *testing.T) {
	song := testSong()
	d := newTestService(t, song, 360, 20)
	userID := uuid.New()

	got, positions, err := d.svc.Enqueue(context.Background(), userID, song.ID, validWAVReader())
	require.NoError(t, err)
	require.Equal(t, domain.AnalysisStatusQueued, got.Status)
	require.NotNil(t, got.QueuePosition)
	require.Equal(t, 1, *got.QueuePosition)
	require.Equal(t, 1, positions[got.ID])
	require.Len(t, d.queue.enqueued, 1)
	require.NotNil(t, got.QueueStreamID)
}

func TestEnqueue_SongNotFound_Rejected(t *testing.T) {
	d := newTestService(t, testSong(), 360, 20)

	_, _, err := d.svc.Enqueue(context.Background(), uuid.New(), uuid.New(), validWAVReader())
	require.ErrorIs(t, err, domain.ErrNotFound)
	require.Equal(t, 0, d.rate.calls, "rate limit must not be spent on a request that fails song lookup")
}

func TestEnqueue_RateLimited_Rejected(t *testing.T) {
	song := testSong()
	d := newTestService(t, song, 360, 20)
	d.rate.allowed = false

	_, _, err := d.svc.Enqueue(context.Background(), uuid.New(), song.ID, validWAVReader())
	require.ErrorIs(t, err, domain.ErrAnalysisRateLimited)
	var throttled *domain.ThrottledError
	require.ErrorAs(t, err, &throttled)
}

func TestEnqueue_QueueFull_Rejected(t *testing.T) {
	song := testSong()
	d := newTestService(t, song, 360, 20)
	d.queue.length = 20

	_, _, err := d.svc.Enqueue(context.Background(), uuid.New(), song.ID, validWAVReader())
	require.ErrorIs(t, err, domain.ErrQueueFull)
	require.Empty(t, d.queue.enqueued, "a full queue must never publish the job")
}

func TestEnqueue_UnsupportedFormat_Rejected(t *testing.T) {
	song := testSong()
	d := newTestService(t, song, 360, 20)

	_, _, err := d.svc.Enqueue(context.Background(), uuid.New(), song.ID, strings.NewReader("not audio"))
	require.ErrorIs(t, err, domain.ErrUnsupportedAudioFormat)
	require.Equal(t, 0, d.processor.transcodeCalls)
}

func TestEnqueue_TooLong_Rejected(t *testing.T) {
	song := testSong()
	d := newTestService(t, song, 360, 20)
	d.processor.seconds = 500

	_, _, err := d.svc.Enqueue(context.Background(), uuid.New(), song.ID, validWAVReader())
	require.ErrorIs(t, err, domain.ErrAudioTooLong)
	require.Equal(t, 0, d.processor.transcodeCalls)
}

func TestEnqueue_TranscodeError_NoAnalysisCreated(t *testing.T) {
	song := testSong()
	d := newTestService(t, song, 360, 20)
	d.processor.transcodeErr = errBoom

	_, _, err := d.svc.Enqueue(context.Background(), uuid.New(), song.ID, validWAVReader())
	require.Error(t, err)
	require.Empty(t, d.analyses.byID, "a failed transcode must never leave a queued row behind")
}

func TestEnqueue_MultipleJobs_PositionsIncrementInOrder(t *testing.T) {
	song := testSong()
	d := newTestService(t, song, 360, 20)
	ctx := context.Background()
	userID := uuid.New()

	first, _, err := d.svc.Enqueue(ctx, userID, song.ID, validWAVReader())
	require.NoError(t, err)
	require.Equal(t, 1, *first.QueuePosition)

	second, _, err := d.svc.Enqueue(ctx, userID, song.ID, validWAVReader())
	require.NoError(t, err)
	require.Equal(t, 2, *second.QueuePosition)
}
