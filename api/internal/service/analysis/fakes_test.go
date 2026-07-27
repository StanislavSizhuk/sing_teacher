package analysis_test

import (
	"context"
	"fmt"
	"os"
	"sync"
	"time"

	"github.com/google/uuid"

	"ai-vocal-coach/api/internal/domain"
)

// --- fakeAnalysisRepository ------------------------------------------------

// fakeAnalysisRepository keeps insertion order to stand in for queue_seq,
// so RecalculatePositions can reproduce the real FIFO/shift-on-cancel
// behavior the Postgres implementation provides.
type fakeAnalysisRepository struct {
	mu        sync.Mutex
	byID      map[uuid.UUID]*domain.Analysis
	order     []uuid.UUID
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
	a.QueueSeq = int64(len(f.order) + 1)
	f.byID[a.ID] = cloneAnalysis(a)
	f.order = append(f.order, a.ID)
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
	if a.Status != domain.AnalysisStatusQueued {
		return nil, domain.ErrAnalysisNotQueued
	}
	a.Status = domain.AnalysisStatusCanceled
	return cloneAnalysis(a), nil
}

func (f *fakeAnalysisRepository) RecalculatePositions(_ context.Context) (map[uuid.UUID]int, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	changed := map[uuid.UUID]int{}
	pos := 0
	for _, id := range f.order {
		a := f.byID[id]
		if a.Status != domain.AnalysisStatusQueued {
			continue
		}
		pos++
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
	seconds        float64
	probeErr       error
	transcodeErr   error
	transcodeCalls int
}

func (f *fakeAudioProcessor) Probe(_ context.Context, _ string) (float64, error) {
	if f.probeErr != nil {
		return 0, f.probeErr
	}
	return f.seconds, nil
}

func (f *fakeAudioProcessor) Transcode(_ context.Context, _, dst string) error {
	f.transcodeCalls++
	if f.transcodeErr != nil {
		return f.transcodeErr
	}
	return os.WriteFile(dst, []byte("canonical audio bytes"), 0o600)
}

// --- fakeRateLimiter ---------------------------------------------------------

type fakeRateLimiter struct {
	allowed    bool
	retryAfter time.Duration
	err        error
	calls      int
}

func newAllowingRateLimiter() *fakeRateLimiter {
	return &fakeRateLimiter{allowed: true}
}

func (f *fakeRateLimiter) Allow(_ context.Context, _ uuid.UUID) (bool, time.Duration, error) {
	f.calls++
	if f.err != nil {
		return false, 0, f.err
	}
	return f.allowed, f.retryAfter, nil
}

// --- fakeQueueProducer -------------------------------------------------------

type fakeQueueProducer struct {
	length      int64
	lengthErr   error
	enqueueErr  error
	removeErr   error
	nextEntryID int
	enqueued    []uuid.UUID
	removed     []string
}

func (f *fakeQueueProducer) Length(_ context.Context) (int64, error) {
	if f.lengthErr != nil {
		return 0, f.lengthErr
	}
	return f.length, nil
}

func (f *fakeQueueProducer) Enqueue(_ context.Context, analysisID uuid.UUID) (string, error) {
	if f.enqueueErr != nil {
		return "", f.enqueueErr
	}
	f.nextEntryID++
	f.enqueued = append(f.enqueued, analysisID)
	return fmt.Sprintf("%d-0", f.nextEntryID), nil
}

func (f *fakeQueueProducer) Remove(_ context.Context, entryID string) error {
	f.removed = append(f.removed, entryID)
	return f.removeErr
}
