package analysis

import (
	"context"
	"fmt"
	"io"

	"github.com/google/uuid"

	"ai-vocal-coach/api/internal/domain"
	"ai-vocal-coach/api/internal/media"
)

// Enqueue validates and stores recording, then puts a new analysis job in
// the queue and returns it together with the position of every queued
// analysis whose position changed (including this one) -- the caller pushes
// that map over WebSocket (spec 10, FR-22).
//
// Cheap checks run before any expensive work: song existence and prep
// status, rate limit, then queue capacity, all before the recording is even
// read off the wire. If the song's cold path hasn't reached ready yet, the
// job is still created and the recording still stored -- it starts in
// waiting_for_reference instead of queued, and is never published onto
// analyses:run until the song wakes it (spec 6.2, 10.3, FR-16). If the
// song's cold path already failed, the request is rejected outright: no
// point creating a job that can only ever sit waiting for a prep that will
// never retry itself (FR-17).
func (s *Service) Enqueue(ctx context.Context, userID, songID uuid.UUID, recording io.Reader) (a *domain.Analysis, positions map[uuid.UUID]int, err error) {
	song, err := s.songs.GetByID(ctx, songID)
	if err != nil {
		return nil, nil, err
	}
	if song.PrepStatus == domain.SongPrepFailed {
		return nil, nil, domain.ErrReferencePrepFailed
	}
	songReady := song.PrepStatus == domain.SongPrepReady

	allowed, retryAfter, err := s.rateLimiter.Allow(ctx, userID)
	if err != nil {
		return nil, nil, fmt.Errorf("check analysis rate limit: %w", err)
	}
	if !allowed {
		return nil, nil, &domain.ThrottledError{Err: domain.ErrAnalysisRateLimited, RetryAfter: retryAfter}
	}

	if songReady {
		// Only meaningful as a pre-check against analyses:run -- a waiting
		// job never touches that stream yet, so it cannot contribute to or
		// be blocked by this cap (spec 10.1 caps each stream independently).
		queueLen, err := s.queue.Length(ctx)
		if err != nil {
			return nil, nil, fmt.Errorf("check queue length: %w", err)
		}
		if queueLen >= s.queueMaxLength {
			return nil, nil, domain.ErrQueueFull
		}
	}

	rawPath, err := s.files.WriteTemp(recording, s.maxUploadBytes)
	if err != nil {
		return nil, nil, err
	}
	defer func() { _ = s.files.Remove(rawPath) }()

	if _, ok, err := media.SniffFile(rawPath); err != nil {
		return nil, nil, err
	} else if !ok {
		return nil, nil, domain.ErrUnsupportedAudioFormat
	}

	seconds, err := s.processor.Probe(ctx, rawPath)
	if err != nil {
		return nil, nil, err
	}
	if int(seconds) > s.maxAudioSeconds {
		return nil, nil, domain.ErrAudioTooLong
	}

	analysisID := uuid.New()
	canonicalPath := s.files.PathFor(filePrefix, analysisID)
	if err := s.processor.Transcode(ctx, rawPath, canonicalPath); err != nil {
		return nil, nil, fmt.Errorf("transcode recording: %w", err)
	}

	initialStatus := domain.AnalysisStatusWaitingForReference
	if songReady {
		initialStatus = domain.AnalysisStatusQueued
	}
	created := &domain.Analysis{ID: analysisID, UserID: userID, SongID: songID, Status: initialStatus}
	if err := s.analyses.Create(ctx, created); err != nil {
		_ = s.files.Remove(canonicalPath)
		return nil, nil, fmt.Errorf("create analysis: %w", err)
	}

	if !songReady {
		// No ML job exists yet to admit onto analyses:run -- the song's cold
		// path wakes this row into queued and publishes it itself once
		// prep_status reaches ready (spec 10.3).
		return created, nil, nil
	}

	// The queueLen check above is only a cheap pre-check to skip expensive
	// work on an already-full queue; it is not the admission decision.
	// Concurrent callers that all pass it before any of them publish would
	// overshoot queueMaxLength, so the actual admission has to be atomic
	// with the publish itself (spec 10, FR-24).
	streamEntryID, ok, err := s.queue.EnqueueIfUnderLimit(ctx, analysisID, s.queueMaxLength)
	if err != nil {
		// The row now exists as "queued" but was never actually published to
		// Redis. Rare (Redis would have to fail right after Postgres
		// succeeded) and, like a post-commit email failure elsewhere in this
		// codebase (service/auth.Register), left as a known gap rather than
		// building compensating-transaction machinery for it.
		return nil, nil, fmt.Errorf("enqueue analysis: %w", err)
	}
	if !ok {
		// The queue filled up between the pre-check and here (a losing
		// entrant in a concurrent burst). Unlike a genuine Redis error, this
		// is an expected, common outcome under load, so the just-created row
		// is rolled back rather than left behind as a phantom "queued" job
		// that will never actually run.
		_ = s.files.Remove(canonicalPath)
		if delErr := s.analyses.Delete(ctx, analysisID); delErr != nil {
			return nil, nil, fmt.Errorf("roll back analysis after queue-full: %w", delErr)
		}
		return nil, nil, domain.ErrQueueFull
	}
	if err := s.analyses.SetQueueStreamID(ctx, analysisID, streamEntryID); err != nil {
		return nil, nil, fmt.Errorf("record queue stream id: %w", err)
	}
	created.QueueStreamID = &streamEntryID

	positions, err = s.analyses.RecalculatePositions(ctx)
	if err != nil {
		return nil, nil, fmt.Errorf("recalculate queue positions: %w", err)
	}
	if pos, ok := positions[analysisID]; ok {
		created.QueuePosition = &pos
	}
	return created, positions, nil
}
