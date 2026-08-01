package analysis_test

import (
	"context"
	"fmt"
	"os"
	"sort"
	"sync"
	"time"

	"github.com/google/uuid"

	"ai-vocal-coach/api/internal/domain"
)

// --- fakeAnalysisRepository ------------------------------------------------

// fakeAnalysisRepository hands out a monotonic queue_seq per row, mirroring
// the Postgres BIGSERIAL/sequence Create and Retry draw from, so
// RecalculatePositions can reproduce the real FIFO/shift-on-cancel/
// back-of-queue-on-retry behavior the Postgres implementation provides.
type fakeAnalysisRepository struct {
	mu        sync.Mutex
	byID      map[uuid.UUID]*domain.Analysis
	nextSeq   int64
	createErr error
}

func newFakeAnalysisRepository() *fakeAnalysisRepository {
	return &fakeAnalysisRepository{byID: map[uuid.UUID]*domain.Analysis{}}
}

func cloneAnalysis(a *domain.Analysis) *domain.Analysis {
	cp := *a
	return &cp
}

func (f *fakeAnalysisRepository) Create(_ context.Context, a *domain.Analysis) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.createErr != nil {
		err := f.createErr
		f.createErr = nil
		return err
	}
	a.CreatedAt = time.Now()
	f.nextSeq++
	a.QueueSeq = f.nextSeq
	f.byID[a.ID] = cloneAnalysis(a)
	return nil
}

func (f *fakeAnalysisRepository) Delete(_ context.Context, id uuid.UUID) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	delete(f.byID, id)
	return nil
}

func (f *fakeAnalysisRepository) SetQueueStreamID(_ context.Context, id uuid.UUID, streamEntryID string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	a, ok := f.byID[id]
	if !ok {
		return domain.ErrNotFound
	}
	sid := streamEntryID
	a.QueueStreamID = &sid
	return nil
}

func (f *fakeAnalysisRepository) GetByID(_ context.Context, id, userID uuid.UUID) (*domain.Analysis, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	a, ok := f.byID[id]
	if !ok || a.UserID != userID {
		return nil, domain.ErrNotFound
	}
	return cloneAnalysis(a), nil
}

func (f *fakeAnalysisRepository) Cancel(_ context.Context, id, userID uuid.UUID) (*domain.Analysis, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	a, ok := f.byID[id]
	if !ok || a.UserID != userID {
		return nil, domain.ErrNotFound
	}
	if a.Status != domain.AnalysisStatusQueued && a.Status != domain.AnalysisStatusWaitingForReference {
		return nil, domain.ErrAnalysisNotQueued
	}
	a.Status = domain.AnalysisStatusCanceled
	return cloneAnalysis(a), nil
}

func (f *fakeAnalysisRepository) Retry(_ context.Context, id, userID uuid.UUID) (*domain.Analysis, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	a, ok := f.byID[id]
	if !ok || a.UserID != userID {
		return nil, domain.ErrNotFound
	}
	if a.Status != domain.AnalysisStatusFailed {
		return nil, domain.ErrAnalysisNotFailed
	}
	a.Status = domain.AnalysisStatusQueued
	a.ErrorCode = nil
	a.CurrentStage = nil
	a.QueuePosition = nil
	a.QueueStreamID = nil
	f.nextSeq++
	a.QueueSeq = f.nextSeq
	return cloneAnalysis(a), nil
}

func (f *fakeAnalysisRepository) RetryToWaitingForReference(_ context.Context, id, userID uuid.UUID) (*domain.Analysis, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	a, ok := f.byID[id]
	if !ok || a.UserID != userID {
		return nil, domain.ErrNotFound
	}
	if a.Status != domain.AnalysisStatusFailed {
		return nil, domain.ErrAnalysisNotFailed
	}
	a.Status = domain.AnalysisStatusWaitingForReference
	a.ErrorCode = nil
	a.CurrentStage = nil
	a.QueuePosition = nil
	a.QueueStreamID = nil
	f.nextSeq++
	a.QueueSeq = f.nextSeq
	return cloneAnalysis(a), nil
}

func (f *fakeAnalysisRepository) RecalculatePositions(_ context.Context) (map[uuid.UUID]int, error) {
	f.mu.Lock()
	defer f.mu.Unlock()

	var queuedIDs []uuid.UUID
	for id, a := range f.byID {
		if a.Status == domain.AnalysisStatusQueued {
			queuedIDs = append(queuedIDs, id)
		}
	}
	sort.Slice(queuedIDs, func(i, j int) bool {
		return f.byID[queuedIDs[i]].QueueSeq < f.byID[queuedIDs[j]].QueueSeq
	})

	changed := map[uuid.UUID]int{}
	for i, id := range queuedIDs {
		pos := i + 1
		a := f.byID[id]
		if a.QueuePosition == nil || *a.QueuePosition != pos {
			p := pos
			a.QueuePosition = &p
			changed[id] = pos
		}
	}
	return changed, nil
}

// --- fakeSongRepository -----------------------------------------------------

type fakeSongRepository struct {
	byID map[uuid.UUID]*domain.Song
}

func newFakeSongRepository(songs ...*domain.Song) *fakeSongRepository {
	f := &fakeSongRepository{byID: map[uuid.UUID]*domain.Song{}}
	for _, s := range songs {
		f.byID[s.ID] = s
	}
	return f
}

func (f *fakeSongRepository) GetByID(_ context.Context, id uuid.UUID) (*domain.Song, error) {
	s, ok := f.byID[id]
	if !ok {
		return nil, domain.ErrNotFound
	}
	return s, nil
}

// --- fakeAudioProcessor -----------------------------------------------------

type fakeAudioProcessor struct {
	mu             sync.Mutex
	seconds        float64
	probeErr       error
	transcodeErr   error
	transcodeCalls int
}

func (f *fakeAudioProcessor) Probe(_ context.Context, _ string) (float64, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.probeErr != nil {
		return 0, f.probeErr
	}
	return f.seconds, nil
}

func (f *fakeAudioProcessor) Transcode(_ context.Context, _, dst string) error {
	f.mu.Lock()
	f.transcodeCalls++
	transcodeErr := f.transcodeErr
	f.mu.Unlock()
	if transcodeErr != nil {
		return transcodeErr
	}
	return os.WriteFile(dst, []byte("canonical audio bytes"), 0o600)
}

// --- fakeRateLimiter ---------------------------------------------------------

type fakeRateLimiter struct {
	mu         sync.Mutex
	allowed    bool
	retryAfter time.Duration
	err        error
	calls      int
}

func newAllowingRateLimiter() *fakeRateLimiter {
	return &fakeRateLimiter{allowed: true}
}

func (f *fakeRateLimiter) Allow(_ context.Context, _ uuid.UUID) (bool, time.Duration, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.calls++
	if f.err != nil {
		return false, 0, f.err
	}
	return f.allowed, f.retryAfter, nil
}

// --- fakeQueueProducer -------------------------------------------------------

// fakeQueueProducer guards length/enqueued with a mutex so
// EnqueueIfUnderLimit can reproduce the real Producer's atomicity guarantee
// (spec 10, FR-24) under a concurrent test, not just a sequential one.
type fakeQueueProducer struct {
	mu                sync.Mutex
	length            int64
	lengthErr         error
	enqueueErr        error
	enqueueIfUnderErr error
	// forceFull makes EnqueueIfUnderLimit report the queue as full
	// regardless of length, simulating a concurrent racer winning the
	// atomic admission after this caller already passed the early
	// Length() pre-check.
	forceFull   bool
	removeErr   error
	nextEntryID int
	enqueued    []uuid.UUID
	removed     []string
}

func (f *fakeQueueProducer) Length(_ context.Context) (int64, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.lengthErr != nil {
		return 0, f.lengthErr
	}
	return f.length, nil
}

func (f *fakeQueueProducer) Enqueue(_ context.Context, analysisID uuid.UUID) (string, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.enqueueErr != nil {
		return "", f.enqueueErr
	}
	f.nextEntryID++
	f.length++
	f.enqueued = append(f.enqueued, analysisID)
	return fmt.Sprintf("%d-0", f.nextEntryID), nil
}

func (f *fakeQueueProducer) EnqueueIfUnderLimit(_ context.Context, analysisID uuid.UUID, maxLen int64) (string, bool, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.enqueueIfUnderErr != nil {
		return "", false, f.enqueueIfUnderErr
	}
	if f.forceFull || f.length >= maxLen {
		return "", false, nil
	}
	f.nextEntryID++
	f.length++
	f.enqueued = append(f.enqueued, analysisID)
	return fmt.Sprintf("%d-0", f.nextEntryID), true, nil
}

func (f *fakeQueueProducer) Remove(_ context.Context, entryID string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.removed = append(f.removed, entryID)
	return f.removeErr
}
