//go:build integration

package queue_test

import (
	"context"
	"sync"
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
	p := queue.NewProducer(client, "test:queue", "test:workers")
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
	p := queue.NewProducer(client, "test:queue", "test:workers")
	ctx := context.Background()
	require.NoError(t, p.EnsureGroup(ctx))

	entryID, err := p.Enqueue(ctx, uuid.New())
	require.NoError(t, err)

	require.NoError(t, p.Remove(ctx, entryID))

	length, err := p.Length(ctx)
	require.NoError(t, err)
	require.Equal(t, int64(0), length)
}

func TestProducer_EnqueueIfUnderLimit_RejectsOnceAtCap(t *testing.T) {
	client := setupRedis(t)
	p := queue.NewProducer(client, "test:queue", "test:workers")
	ctx := context.Background()
	require.NoError(t, p.EnsureGroup(ctx))

	_, ok, err := p.EnqueueIfUnderLimit(ctx, uuid.New(), 1)
	require.NoError(t, err)
	require.True(t, ok)

	_, ok, err = p.EnqueueIfUnderLimit(ctx, uuid.New(), 1)
	require.NoError(t, err)
	require.False(t, ok, "a second admission must be rejected once the queue is at maxLen")

	length, err := p.Length(ctx)
	require.NoError(t, err)
	require.Equal(t, int64(1), length, "a rejected admission must never publish a stream entry")
}

// TestProducer_EnqueueIfUnderLimit_ConcurrentBurst_NeverExceedsCap is the
// real-Redis regression test for the queue-admission race E6's load test
// surfaced: a naive XLEN-then-XADD lets every concurrent caller read the
// same pre-publish length and all decide to publish, overshooting maxLen by
// as many requests as raced together. Firing a burst well past maxLen at a
// real Redis instance (not a mutex-guarded fake) proves the Lua script's
// atomicity actually holds under genuine concurrent network clients, not
// just in-process goroutines sharing memory (spec 10, FR-24).
func TestProducer_EnqueueIfUnderLimit_ConcurrentBurst_NeverExceedsCap(t *testing.T) {
	client := setupRedis(t)
	p := queue.NewProducer(client, "test:queue", "test:workers")
	ctx := context.Background()
	require.NoError(t, p.EnsureGroup(ctx))

	const maxLen = 20
	const burst = 40

	start := make(chan struct{})
	admittedFlags := make([]bool, burst)
	errs := make([]error, burst)
	var wg sync.WaitGroup
	for i := range admittedFlags {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			<-start
			_, ok, err := p.EnqueueIfUnderLimit(ctx, uuid.New(), maxLen)
			admittedFlags[i] = ok
			errs[i] = err
		}(i)
	}
	close(start)
	wg.Wait()

	var admitted int
	for i, ok := range admittedFlags {
		require.NoError(t, errs[i])
		if ok {
			admitted++
		}
	}
	require.Equal(t, maxLen, admitted, "a concurrent burst past the cap must admit exactly maxLen entries")

	length, err := p.Length(ctx)
	require.NoError(t, err)
	require.Equal(t, int64(maxLen), length, "the stream itself must never exceed maxLen either")
}

func TestProducer_EnsureGroup_IdempotentOnRepeatCalls(t *testing.T) {
	client := setupRedis(t)
	p := queue.NewProducer(client, "test:queue", "test:workers")
	ctx := context.Background()

	require.NoError(t, p.EnsureGroup(ctx))
	require.NoError(t, p.EnsureGroup(ctx), "a second call must not error (BUSYGROUP tolerated)")
}
