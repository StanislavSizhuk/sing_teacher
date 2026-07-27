//go:build integration

package postgres_test

import (
	"context"
	"fmt"
	"testing"

	"github.com/google/uuid"
	"github.com/stretchr/testify/require"

	"ai-vocal-coach/api/internal/domain"
	"ai-vocal-coach/api/internal/repository/postgres"
)

func newTestSong(contentHash string) *domain.Song {
	return &domain.Song{
		ID:          uuid.New(),
		SourceType:  domain.SongSourceUpload,
		ContentHash: contentHash,
		Title:       "Test Song",
		DurationSec: 180,
	}
}

func TestSongRepository_GetOrCreate_NewContentHash_Creates(t *testing.T) {
	pool := setupPostgres(t)
	repo := postgres.NewSongRepository(pool)
	ctx := context.Background()

	song := newTestSong(fmt.Sprintf("hash-%s", uuid.NewString()))
	got, created, err := repo.GetOrCreate(ctx, song)
	require.NoError(t, err)
	require.True(t, created)
	require.Equal(t, song.ID, got.ID)
	require.False(t, got.CreatedAt.IsZero())
}

func TestSongRepository_GetOrCreate_ExistingContentHash_ReusesRow(t *testing.T) {
	pool := setupPostgres(t)
	repo := postgres.NewSongRepository(pool)
	ctx := context.Background()

	hash := fmt.Sprintf("hash-%s", uuid.NewString())
	first, created, err := repo.GetOrCreate(ctx, newTestSong(hash))
	require.NoError(t, err)
	require.True(t, created)

	// A second submission with the same content hash (spec 6.6 dedup, FR-13)
	// must reuse the first row, not create a second one -- even with a
	// different server-generated id and title.
	second := newTestSong(hash)
	second.Title = "Different Title From A Duplicate Upload"
	got, created, err := repo.GetOrCreate(ctx, second)
	require.NoError(t, err)
	require.False(t, created)
	require.Equal(t, first.ID, got.ID)
	require.Equal(t, first.Title, got.Title)
}

func TestSongRepository_GetByID_NotFound(t *testing.T) {
	pool := setupPostgres(t)
	repo := postgres.NewSongRepository(pool)

	_, err := repo.GetByID(context.Background(), uuid.New())
	require.ErrorIs(t, err, domain.ErrNotFound)
}
