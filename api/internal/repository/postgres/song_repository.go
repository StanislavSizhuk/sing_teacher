package postgres

import (
	"context"
	"errors"
	"fmt"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"

	"ai-vocal-coach/api/internal/domain"
)

// SongRepository persists domain.Song rows in Postgres.
type SongRepository struct {
	pool *pgxpool.Pool
}

// NewSongRepository builds a SongRepository backed by the given pool.
func NewSongRepository(pool *pgxpool.Pool) *SongRepository {
	return &SongRepository{pool: pool}
}

const songColumns = `id, source_type, source_url, content_hash, title, artist, duration_sec,
	lyrics_json, lyrics_available, reference_pitch, vocal_stem_path,
	prep_status, prep_stage, prep_error_code, prepared_at, created_at`

func scanSong(row pgx.Row) (*domain.Song, error) {
	var s domain.Song
	err := row.Scan(&s.ID, &s.SourceType, &s.SourceURL, &s.ContentHash, &s.Title, &s.Artist, &s.DurationSec,
		&s.LyricsJSON, &s.LyricsAvailable, &s.ReferencePitch, &s.VocalStemPath,
		&s.PrepStatus, &s.PrepStage, &s.PrepErrorCode, &s.PreparedAt, &s.CreatedAt)
	if err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, domain.ErrNotFound
		}
		return nil, fmt.Errorf("scan song: %w", err)
	}
	return &s, nil
}

// GetByID returns the song with this id.
func (r *SongRepository) GetByID(ctx context.Context, id uuid.UUID) (*domain.Song, error) {
	q := `SELECT ` + songColumns + ` FROM songs WHERE id = $1`
	return scanSong(r.pool.QueryRow(ctx, q, id))
}

// Delete removes a row outright. The only caller is
// song.Service.enqueuePrep, undoing its own just-created row when
// songs:prep turns out to have filled up between the pre-check and the
// atomic admission -- never a user-reachable operation.
func (r *SongRepository) Delete(ctx context.Context, id uuid.UUID) error {
	const q = `DELETE FROM songs WHERE id = $1`
	if _, err := r.pool.Exec(ctx, q, id); err != nil {
		return fmt.Errorf("delete song: %w", err)
	}
	return nil
}

// GetOrCreate inserts song unless a row with its ContentHash already exists,
// in which case that existing row is returned instead (spec 6.6 cache key,
// FR-13 dedup). created reports which branch was taken.
func (r *SongRepository) GetOrCreate(ctx context.Context, song *domain.Song) (result *domain.Song, created bool, err error) {
	const insertQ = `
		INSERT INTO songs (id, source_type, source_url, content_hash, title, artist, duration_sec, created_at)
		VALUES ($1, $2, $3, $4, $5, $6, $7, now())
		ON CONFLICT (content_hash) DO NOTHING
		RETURNING ` + songColumns

	inserted, err := scanSong(r.pool.QueryRow(ctx, insertQ,
		song.ID, song.SourceType, song.SourceURL, song.ContentHash, song.Title, song.Artist, song.DurationSec))
	if err == nil {
		return inserted, true, nil
	}
	if !errors.Is(err, domain.ErrNotFound) {
		return nil, false, err
	}

	// ON CONFLICT DO NOTHING returned no row: another request already holds
	// this content_hash. Fetch it instead of failing the caller.
	const getQ = `SELECT ` + songColumns + ` FROM songs WHERE content_hash = $1`
	existing, err := scanSong(r.pool.QueryRow(ctx, getQ, song.ContentHash))
	if err != nil {
		return nil, false, fmt.Errorf("look up existing song by content hash: %w", err)
	}
	return existing, false, nil
}

// RetryPrep resets a song stuck in a failed cold path back to pending so it
// can be re-enqueued onto songs:prep (FR-17), without touching its stored
// audio or any already-cached partial P-stage results the worker's
// resumability logic (prep_stages_json) can still reuse. It returns
// domain.ErrSongPrepNotFailed if the song exists but prep_status isn't
// 'failed'.
func (r *SongRepository) RetryPrep(ctx context.Context, id uuid.UUID) (*domain.Song, error) {
	const q = `
		UPDATE songs
		SET prep_status = 'pending', prep_error_code = NULL, prep_stage = NULL
		WHERE id = $1 AND prep_status = 'failed'
		RETURNING ` + songColumns

	updated, err := scanSong(r.pool.QueryRow(ctx, q, id))
	if err == nil {
		return updated, nil
	}
	if !errors.Is(err, domain.ErrNotFound) {
		return nil, err
	}

	// The UPDATE matched no row: find out why so the caller gets the right
	// sentinel instead of a blanket "not found".
	existing, getErr := r.GetByID(ctx, id)
	if getErr != nil {
		return nil, getErr
	}
	if existing.PrepStatus != domain.SongPrepFailed {
		return nil, domain.ErrSongPrepNotFailed
	}
	// Existed and was failed moments ago but the UPDATE still matched zero
	// rows: raced with a concurrent retry. Report the same outcome either way.
	return nil, domain.ErrSongPrepNotFailed
}
