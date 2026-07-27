//go:build integration

package queue_test

import (
	"context"
	"testing"

	"github.com/google/uuid"
	"github.com/redis/go-redis/v9"
	"github.com/stretchr/testify/require"
	"github.com/testcontainers/testcontainers-go"
	tcredis "github.com/testcontainers/testcontainers-go/modules/redis"

	"ai-vocal-coach/api/internal/queue"
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

func TestProducer_Enqueue_IncreasesLength(t *testing.T) {
	client := setupRedis(t)
	p := queue.NewProducer(client)
	ctx := context.Background()
	require.NoError(t, p.EnsureGroup(ctx))

	before, err := p.Length(ctx)
	require.NoError(t, err)
	require.Equal(t, int64(0), before)

	_, err = p.Enqueue(ctx, uuid.New())
	require.NoError(t, err)

	after, err := p.Length(ctx)
	require.NoError(t, err)
	require.Equal(t, int64(1), after)
}

func TestProducer_Remove_DecreasesLength(t *testing.T) {
	client := setupRedis(t)
	p := queue.NewProducer(client)
	ctx := context.Background()
	require.NoError(t, p.EnsureGroup(ctx))

	entryID, err := p.Enqueue(ctx, uuid.New())
	require.NoError(t, err)

	require.NoError(t, p.Remove(ctx, entryID))

	length, err := p.Length(ctx)
	require.NoError(t, err)
	require.Equal(t, int64(0), length)
}

func TestProducer_EnsureGroup_IdempotentOnRepeatCalls(t *testing.T) {
	client := setupRedis(t)
	p := queue.NewProducer(client)
	ctx := context.Background()

	require.NoError(t, p.EnsureGroup(ctx))
	require.NoError(t, p.EnsureGroup(ctx), "a second call must not error (BUSYGROUP tolerated)")
}
