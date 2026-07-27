//go:build integration

package redisrepo_test

import (
	"context"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/redis/go-redis/v9"
	"github.com/stretchr/testify/require"
	"github.com/testcontainers/testcontainers-go"
	tcredis "github.com/testcontainers/testcontainers-go/modules/redis"

	"ai-vocal-coach/api/internal/domain"
	"ai-vocal-coach/api/internal/repository/redisrepo"
)

func setupRedis(t *testing.T) *redis.Client {
	t.Helper()
	ctx := context.Background()

	container, err := tcredis.Run(ctx, "redis:7-alpine")
	require.NoError(t, err)
	t.Cleanup(func() {
		require.NoError(t, testcontainers.TerminateContainer(container))
	})

	connStr, err := container.ConnectionString(ctx)
	require.NoError(t, err)

	opts, err := redis.ParseURL(connStr)
	require.NoError(t, err)
	client := redis.NewClient(opts)
	t.Cleanup(func() { require.NoError(t, client.Close()) })

	require.NoError(t, client.Ping(ctx).Err())
	return client
}

// --- RefreshTokenStore -------------------------------------------------------

func TestRefreshTokenStore_IssueAndRotate(t *testing.T) {
	client := setupRedis(t)
	store := redisrepo.NewRefreshTokenStore(client, time.Hour)
	ctx := context.Background()
	userID := uuid.New()

	token, err := store.Issue(ctx, userID)
	require.NoError(t, err)
	require.NotEmpty(t, token)

	newToken, gotUser, err := store.Rotate(ctx, token)
	require.NoError(t, err)
	require.Equal(t, userID, gotUser)
	require.NotEqual(t, token, newToken)
}

func TestRefreshTokenStore_ReuseDetected_RevokesFamily(t *testing.T) {
	client := setupRedis(t)
	store := redisrepo.NewRefreshTokenStore(client, time.Hour)
	ctx := context.Background()
	userID := uuid.New()

	token, err := store.Issue(ctx, userID)
	require.NoError(t, err)

	newToken, _, err := store.Rotate(ctx, token)
	require.NoError(t, err)

	// Replaying the token that was already rotated away simulates a stolen
	// refresh token being used after the legitimate client has moved on.
	_, _, err = store.Rotate(ctx, token)
	require.ErrorIs(t, err, domain.ErrRefreshTokenReused)

	// The whole family -- including the legitimate successor -- must now be dead.
	_, _, err = store.Rotate(ctx, newToken)
	require.ErrorIs(t, err, domain.ErrRefreshTokenInvalid)
}

func TestRefreshTokenStore_UnknownToken_Invalid(t *testing.T) {
	client := setupRedis(t)
	store := redisrepo.NewRefreshTokenStore(client, time.Hour)

	_, _, err := store.Rotate(context.Background(), "never-issued")
	require.ErrorIs(t, err, domain.ErrRefreshTokenInvalid)
}

func TestRefreshTokenStore_Revoke_OnlyKillsThatToken(t *testing.T) {
	client := setupRedis(t)
	store := redisrepo.NewRefreshTokenStore(client, time.Hour)
	ctx := context.Background()

	token, err := store.Issue(ctx, uuid.New())
	require.NoError(t, err)
	require.NoError(t, store.Revoke(ctx, token))

	_, _, err = store.Rotate(ctx, token)
	require.ErrorIs(t, err, domain.ErrRefreshTokenInvalid)
}

func TestRefreshTokenStore_RevokeAllForUser(t *testing.T) {
	client := setupRedis(t)
	store := redisrepo.NewRefreshTokenStore(client, time.Hour)
	ctx := context.Background()
	userID := uuid.New()

	tokenA, err := store.Issue(ctx, userID)
	require.NoError(t, err)
	tokenB, err := store.Issue(ctx, userID)
	require.NoError(t, err)

	require.NoError(t, store.RevokeAllForUser(ctx, userID))

	_, _, err = store.Rotate(ctx, tokenA)
	require.ErrorIs(t, err, domain.ErrRefreshTokenInvalid)
	_, _, err = store.Rotate(ctx, tokenB)
	require.ErrorIs(t, err, domain.ErrRefreshTokenInvalid)
}

// --- LoginThrottle -----------------------------------------------------------

func TestLoginThrottle_ExponentialBackoffBeforeLockout(t *testing.T) {
	client := setupRedis(t)
	throttle := redisrepo.NewLoginThrottle(client)
	ctx := context.Background()
	key := "test-key-" + uuid.NewString()

	require.NoError(t, throttle.RecordFailure(ctx, key))

	// Check() reports "locked" for the short inter-attempt backoff too, not
	// just the hard lockout: from the caller's perspective both mean "wait
	// retryAfter before trying again" (spec 9.1's "exponential delay").
	locked, retryAfter, err := throttle.Check(ctx, key)
	require.NoError(t, err)
	require.True(t, locked, "even a single failure imposes a short backoff")
	require.LessOrEqual(t, retryAfter, 60*time.Second)
	require.Less(t, retryAfter, 15*time.Minute, "one failure must not trigger the full lockout")
}

func TestLoginThrottle_LocksAfterMaxFailures(t *testing.T) {
	client := setupRedis(t)
	throttle := redisrepo.NewLoginThrottle(client)
	ctx := context.Background()
	key := "test-key-" + uuid.NewString()

	// Fire all 10 failures back to back rather than checking in between:
	// Check() would otherwise report "locked" from the very first failure's
	// short backoff window, well before this threshold is what trips it.
	for i := 0; i < 10; i++ {
		require.NoError(t, throttle.RecordFailure(ctx, key))
	}

	locked, retryAfter, err := throttle.Check(ctx, key)
	require.NoError(t, err)
	require.True(t, locked)
	require.InDelta(t, (15 * time.Minute).Seconds(), retryAfter.Seconds(), 5,
		"the 10th failure must trigger the full lockout, not just another short backoff step")
}

func TestLoginThrottle_Reset_ClearsState(t *testing.T) {
	client := setupRedis(t)
	throttle := redisrepo.NewLoginThrottle(client)
	ctx := context.Background()
	key := "test-key-" + uuid.NewString()

	require.NoError(t, throttle.RecordFailure(ctx, key))
	require.NoError(t, throttle.Reset(ctx, key))

	locked, _, err := throttle.Check(ctx, key)
	require.NoError(t, err)
	require.False(t, locked)
}

// --- VerificationThrottle ------------------------------------------------------

func TestVerificationThrottle_Cooldown(t *testing.T) {
	client := setupRedis(t)
	throttle := redisrepo.NewVerificationThrottle(client)
	ctx := context.Background()
	userID := uuid.New()

	allowed, _, _, err := throttle.AllowResend(ctx, userID)
	require.NoError(t, err)
	require.True(t, allowed)

	allowed, retryAfter, dailyLimit, err := throttle.AllowResend(ctx, userID)
	require.NoError(t, err)
	require.False(t, allowed, "a second resend inside the cooldown window must be rejected")
	require.False(t, dailyLimit)
	require.Positive(t, retryAfter)
}

func TestVerificationThrottle_DailyLimit(t *testing.T) {
	client := setupRedis(t)
	throttle := redisrepo.NewVerificationThrottle(client)
	ctx := context.Background()
	userID := uuid.New()

	// Bypass the 60s cooldown between sends by clearing its key directly --
	// this test is about the daily cap, not the cooldown, and the alternative
	// is a real 4-minute sleep.
	cooldownKey := "auth:verify-cooldown:" + userID.String()
	for i := 0; i < 5; i++ {
		require.NoError(t, client.Del(ctx, cooldownKey).Err())
		allowed, _, _, err := throttle.AllowResend(ctx, userID)
		require.NoError(t, err)
		require.True(t, allowed, "send %d of the daily allowance should be allowed", i+1)
	}

	require.NoError(t, client.Del(ctx, cooldownKey).Err())
	allowed, _, dailyLimit, err := throttle.AllowResend(ctx, userID)
	require.NoError(t, err)
	require.False(t, allowed)
	require.True(t, dailyLimit, "the 6th send today must be rejected as the daily cap, not the cooldown")
}
