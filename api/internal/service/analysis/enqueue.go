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
// Cheap checks run before any expensive work: song existence, rate limit,
// then queue capacity, all before the recording is even read off the wire.
func (s *Service) Enqueue(ctx context.Context, userID, songID uuid.UUID, recording io.Reader) (a *domain.Analysis, positions map[uuid.UUID]int, err error) {
	if _, err := s.songs.GetByID(ctx, songID); err != nil {
		return nil, nil, err
	}

	allowed, retryAfter, err := s.rateLimiter.Allow(ctx, userID)
	if err != nil {
		return nil, nil, fmt.Errorf("check analysis rate limit: %w", err)
	}
	if !allowed {
		return nil, nil, &domain.ThrottledError{Err: domain.ErrAnalysisRateLimited, RetryAfter: retryAfter}
	}

	queueLen, err := s.queue.Length(ctx)
	if err != nil {
		return nil, nil, fmt.Errorf("check queue length: %w", err)
	}
	if queueLen >= s.queueMaxLength {
		return nil, nil, domain.ErrQueueFull
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

	created := &domain.Analysis{ID: analysisID, UserID: userID, SongID: songID, Status: domain.AnalysisStatusQueued}
	if err := s.analyses.Create(ctx, created); err != nil {
		_ = s.files.Remove(canonicalPath)
		return nil, nil, fmt.Errorf("create analysis: %w", err)
	}

	streamEntryID, err := s.queue.Enqueue(ctx, analysisID)
	if err != nil {
		// The row now exists as "queued" but was never actually published to
		// Redis. Rare (Redis would have to fail right after Postgres
		// succeeded) and, like a post-commit email failure elsewhere in this
		// codebase (service/auth.Register), left as a known gap rather than
		// building compensating-transaction machinery for it.
		return nil, nil, fmt.Errorf("enqueue analysis: %w", err)
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
