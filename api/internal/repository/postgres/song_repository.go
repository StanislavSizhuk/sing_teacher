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
	lyrics_json, reference_pitch_json, vocal_stem_processed, created_at`

func scanSong(row pgx.Row) (*domain.Song, error) {
	var s domain.Song
	err := row.Scan(&s.ID, &s.SourceType, &s.SourceURL, &s.ContentHash, &s.Title, &s.Artist, &s.DurationSec,
		&s.LyricsJSON, &s.ReferencePitchJSON, &s.VocalStemProcessed, &s.CreatedAt)
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
