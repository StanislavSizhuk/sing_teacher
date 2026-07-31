package analysis_test

import (
	"context"
	"errors"
	"strings"
	"sync"
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
	return &domain.Song{
		ID: uuid.New(), SourceType: domain.SongSourceUpload, ContentHash: "h", Title: "T", DurationSec: 200,
		PrepStatus: domain.SongPrepReady,
	}
}

func waitingSong() *domain.Song {
	s := testSong()
	s.PrepStatus = domain.SongPrepPending
	return s
}

func failedPrepSong() *domain.Song {
	s := testSong()
	s.PrepStatus = domain.SongPrepFailed
	return s
}

func validWAVReader() *strings.Reader {
	return strings.NewReader("RIFF____WAVEfmt \x00")
}

func TestEnqueue_Success_ReturnsAnalysisAndPosition(t *testing.T) {
	song := testSong()
	d := newTestService(t, song, 360, 20)
	userID := uuid.New()

	got, positions, err := d.svc.Enqueue(context.Background(), userID, song.ID, domain.AnalysisModeClean, false, validWAVReader())
	require.NoError(t, err)
	require.Equal(t, domain.AnalysisStatusQueued, got.Status)
	require.NotNil(t, got.QueuePosition)
	require.Equal(t, 1, *got.QueuePosition)
	require.Equal(t, 1, positions[got.ID])
	require.Len(t, d.queue.enqueued, 1)
	require.NotNil(t, got.QueueStreamID)
}

// TestEnqueue_StoresModeAndAllowTransposition covers FR-27/FR-31: the
// caller's own mode choice (already validated/defaulted by the transport
// layer) must reach the stored row unchanged -- this is what the worker's
// AnalysisContext (M4) ends up building the pipeline from.
func TestEnqueue_StoresModeAndAllowTransposition(t *testing.T) {
	song := testSong()
	d := newTestService(t, song, 360, 20)

	got, _, err := d.svc.Enqueue(context.Background(), uuid.New(), song.ID, domain.AnalysisModeMixed, true, validWAVReader())
	require.NoError(t, err)
	require.Equal(t, domain.AnalysisModeMixed, got.Mode)
	require.True(t, got.AllowTransposition)

	stored, err := d.analyses.GetByID(context.Background(), got.ID, got.UserID)
	require.NoError(t, err)
	require.Equal(t, domain.AnalysisModeMixed, stored.Mode)
	require.True(t, stored.AllowTransposition)
}

// TestEnqueue_SongNotReady_WaitsForReference is T11's Go-service half (spec
// 6.2, 10.3, FR-16): a song whose cold path hasn't reached ready yet still
// accepts the analysis -- it just holds in waiting_for_reference instead of
// queued, and is never published onto analyses:run (no queue position, no
// stream entry) until the song's prep wakes it.
func TestEnqueue_SongNotReady_WaitsForReference(t *testing.T) {
	song := waitingSong()
	d := newTestService(t, song, 360, 20)

	got, positions, err := d.svc.Enqueue(context.Background(), uuid.New(), song.ID, domain.AnalysisModeClean, false, validWAVReader())
	require.NoError(t, err)
	require.Equal(t, domain.AnalysisStatusWaitingForReference, got.Status)
	require.Nil(t, got.QueuePosition)
	require.Nil(t, got.QueueStreamID)
	require.Empty(t, positions)
	require.Empty(t, d.queue.enqueued, "a waiting analysis must never publish onto analyses:run")
	require.Len(t, d.analyses.byID, 1, "the job is still created, just held, so a retry/upload is never required later")
}

// TestEnqueue_SongPrepFailed_Rejected covers FR-17: no point creating a
// waiting job for a song whose cold path is already known to have failed.
func TestEnqueue_SongPrepFailed_Rejected(t *testing.T) {
	song := failedPrepSong()
	d := newTestService(t, song, 360, 20)

	_, _, err := d.svc.Enqueue(context.Background(), uuid.New(), song.ID, domain.AnalysisModeClean, false, validWAVReader())
	require.ErrorIs(t, err, domain.ErrReferencePrepFailed)
	require.Empty(t, d.analyses.byID, "a known-failed prep must never create a job")
	require.Equal(t, 0, d.rate.calls, "rate limit must not be spent on a request rejected for a failed song")
}

func TestEnqueue_SongNotFound_Rejected(t *testing.T) {
	d := newTestService(t, testSong(), 360, 20)

	_, _, err := d.svc.Enqueue(context.Background(), uuid.New(), uuid.New(), domain.AnalysisModeClean, false, validWAVReader())
	require.ErrorIs(t, err, domain.ErrNotFound)
	require.Equal(t, 0, d.rate.calls, "rate limit must not be spent on a request that fails song lookup")
}

func TestEnqueue_RateLimited_Rejected(t *testing.T) {
	song := testSong()
	d := newTestService(t, song, 360, 20)
	d.rate.allowed = false

	_, _, err := d.svc.Enqueue(context.Background(), uuid.New(), song.ID, domain.AnalysisModeClean, false, validWAVReader())
	require.ErrorIs(t, err, domain.ErrAnalysisRateLimited)
	var throttled *domain.ThrottledError
	require.ErrorAs(t, err, &throttled)
}

func TestEnqueue_QueueFull_Rejected(t *testing.T) {
	song := testSong()
	d := newTestService(t, song, 360, 20)
	d.queue.length = 20

	_, _, err := d.svc.Enqueue(context.Background(), uuid.New(), song.ID, domain.AnalysisModeClean, false, validWAVReader())
	require.ErrorIs(t, err, domain.ErrQueueFull)
	require.Empty(t, d.queue.enqueued, "a full queue must never publish the job")
}

func TestEnqueue_UnsupportedFormat_Rejected(t *testing.T) {
	song := testSong()
	d := newTestService(t, song, 360, 20)

	_, _, err := d.svc.Enqueue(context.Background(), uuid.New(), song.ID, domain.AnalysisModeClean, false, strings.NewReader("not audio"))
	require.ErrorIs(t, err, domain.ErrUnsupportedAudioFormat)
	require.Equal(t, 0, d.processor.transcodeCalls)
}

func TestEnqueue_TooLong_Rejected(t *testing.T) {
	song := testSong()
	d := newTestService(t, song, 360, 20)
	d.processor.seconds = 500

	_, _, err := d.svc.Enqueue(context.Background(), uuid.New(), song.ID, domain.AnalysisModeClean, false, validWAVReader())
	require.ErrorIs(t, err, domain.ErrAudioTooLong)
	require.Equal(t, 0, d.processor.transcodeCalls)
}

func TestEnqueue_TranscodeError_NoAnalysisCreated(t *testing.T) {
	song := testSong()
	d := newTestService(t, song, 360, 20)
	d.processor.transcodeErr = errBoom

	_, _, err := d.svc.Enqueue(context.Background(), uuid.New(), song.ID, domain.AnalysisModeClean, false, validWAVReader())
	require.Error(t, err)
	require.Empty(t, d.analyses.byID, "a failed transcode must never leave a queued row behind")
}

func TestEnqueue_MultipleJobs_PositionsIncrementInOrder(t *testing.T) {
	song := testSong()
	d := newTestService(t, song, 360, 20)
	ctx := context.Background()
	userID := uuid.New()

	first, _, err := d.svc.Enqueue(ctx, userID, song.ID, domain.AnalysisModeClean, false, validWAVReader())
	require.NoError(t, err)
	require.Equal(t, 1, *first.QueuePosition)

	second, _, err := d.svc.Enqueue(ctx, userID, song.ID, domain.AnalysisModeClean, false, validWAVReader())
	require.NoError(t, err)
	require.Equal(t, 2, *second.QueuePosition)
}

// TestEnqueue_QueueFillsBetweenPreCheckAndAdmission_RollsBackRow covers the
// window between Length()'s early pre-check and EnqueueIfUnderLimit's atomic
// admission: a concurrent racer can fill the queue in that gap. The row
// Create wrote in anticipation of admission must be rolled back, not left
// behind as a "queued" job that was never actually published (spec 10, FR-24).
func TestEnqueue_QueueFillsBetweenPreCheckAndAdmission_RollsBackRow(t *testing.T) {
	song := testSong()
	d := newTestService(t, song, 360, 20)
	d.queue.forceFull = true

	_, _, err := d.svc.Enqueue(context.Background(), uuid.New(), song.ID, domain.AnalysisModeClean, false, validWAVReader())
	require.ErrorIs(t, err, domain.ErrQueueFull)
	require.Empty(t, d.queue.enqueued, "a losing entrant must never publish the job")
	require.Empty(t, d.analyses.byID, "the pre-admission row must be rolled back, not left as a phantom queued job")
}

// TestEnqueue_ConcurrentBurst_NeverExceedsQueueMaxLength is the regression
// test for the check-then-act race the E6 load test surfaced: a naive
// Length()-then-Enqueue would let every goroutine in a simultaneous burst
// pass the length check before any of them published, overshooting
// queueMaxLength by as much as the burst size. With EnqueueIfUnderLimit,
// exactly queueMaxLength of a larger concurrent burst must be admitted, and
// every other request must fail cleanly with domain.ErrQueueFull.
func TestEnqueue_ConcurrentBurst_NeverExceedsQueueMaxLength(t *testing.T) {
	const queueMaxLength = 20
	const burst = 35

	song := testSong()
	d := newTestService(t, song, 360, queueMaxLength)

	start := make(chan struct{})
	results := make([]error, burst)
	var wg sync.WaitGroup
	for i := range results {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			<-start
			_, _, err := d.svc.Enqueue(context.Background(), uuid.New(), song.ID, domain.AnalysisModeClean, false, validWAVReader())
			results[i] = err
		}(i)
	}
	close(start)
	wg.Wait()

	var admitted, rejected int
	for _, err := range results {
		switch {
		case err == nil:
			admitted++
		case errors.Is(err, domain.ErrQueueFull):
			rejected++
		default:
			t.Fatalf("unexpected error from concurrent Enqueue: %v", err)
		}
	}
	require.Equal(t, queueMaxLength, admitted, "queue must admit exactly its cap, no more, under a concurrent burst")
	require.Equal(t, burst-queueMaxLength, rejected)
	require.Len(t, d.analyses.byID, queueMaxLength, "rejected entrants must not leave rows behind")
}
