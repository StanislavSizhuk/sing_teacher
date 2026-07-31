package song

import (
	"context"
	"fmt"

	"github.com/google/uuid"

	"ai-vocal-coach/api/internal/domain"
)

// RetryPrep restarts a song's cold path after its previous attempt failed
// (FR-17), without asking the user to re-upload anything: only prep
// bookkeeping is reset, the canonical reference file on disk is untouched.
// It returns domain.ErrSongPrepNotFailed if the song isn't currently
// prep_status='failed'. Mirrors service/analysis.Service.Retry: a cheap
// pre-check against songs:prep's length, then the state reset, then an
// unconditional re-publish -- the same small race window Retry already
// accepts, since this is reinstating a job already admitted once, not a
// fresh admission (spec 10, FR-24 governs new jobs; this is not one).
func (s *Service) RetryPrep(ctx context.Context, id uuid.UUID) (*domain.Song, error) {
	queueLen, err := s.prepQueue.Length(ctx)
	if err != nil {
		return nil, fmt.Errorf("check songs:prep queue length: %w", err)
	}
	if queueLen >= s.prepQueueMaxLen {
		return nil, domain.ErrQueueFull
	}

	retried, err := s.songs.RetryPrep(ctx, id)
	if err != nil {
		return nil, err
	}

	if _, err := s.prepQueue.Enqueue(ctx, id); err != nil {
		return nil, fmt.Errorf("re-enqueue song prep: %w", err)
	}
	return retried, nil
}
