//go:build integration

package redisrepo_test

import (
	"context"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/stretchr/testify/require"

	"ai-vocal-coach/api/internal/repository/redisrepo"
)

func TestAnalysisRateLimiter_AllowsUpToLimit_ThenBlocks(t *testing.T) {
	client := setupRedis(t)
	limiter := redisrepo.NewAnalysisRateLimiter(client, 2, time.Hour)
	ctx := context.Background()
	userID := uuid.New()

	allowed, _, err := limiter.Allow(ctx, userID)
	require.NoError(t, err)
	require.True(t, allowed)

	allowed, _, err = limiter.Allow(ctx, userID)
	require.NoError(t, err)
	require.True(t, allowed)

	allowed, retryAfter, err := limiter.Allow(ctx, userID)
	require.NoError(t, err)
	require.False(t, allowed)
	require.Positive(t, retryAfter)
}

func TestAnalysisRateLimiter_DifferentUsers_IndependentBudgets(t *testing.T) {
	client := setupRedis(t)
	limiter := redisrepo.NewAnalysisRateLimiter(client, 1, time.Hour)
	ctx := context.Background()

	allowedA, _, err := limiter.Allow(ctx, uuid.New())
	require.NoError(t, err)
	require.True(t, allowedA)

	allowedB, _, err := limiter.Allow(ctx, uuid.New())
	require.NoError(t, err)
	require.True(t, allowedB, "a different user must have their own budget")
}

func TestAnalysisRateLimiter_WindowExpiry_FreesSlot(t *testing.T) {
	client := setupRedis(t)
	limiter := redisrepo.NewAnalysisRateLimiter(client, 1, 200*time.Millisecond)
	ctx := context.Background()
	userID := uuid.New()

	allowed, _, err := limiter.Allow(ctx, userID)
	require.NoError(t, err)
	require.True(t, allowed)

	allowed, _, err = limiter.Allow(ctx, userID)
	require.NoError(t, err)
	require.False(t, allowed)

	time.Sleep(250 * time.Millisecond)

	allowed, _, err = limiter.Allow(ctx, userID)
	require.NoError(t, err)
	require.True(t, allowed, "the slot must free up once the window has fully elapsed")
}
