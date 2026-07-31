//go:build integration

package queue_test

import (
	"context"
	"io"
	"log/slog"
	"sync"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/redis/go-redis/v9"
	"github.com/stretchr/testify/require"

	"ai-vocal-coach/api/internal/queue"
)

func testLogger() *slog.Logger {
	return slog.New(slog.NewTextHandler(io.Discard, nil))
}

// waitForSubscriber blocks until Redis reports at least one subscriber on
// EventsChannel -- publishing before RelayEvents' Subscribe call has
// actually taken effect would silently drop the message (Pub/Sub has no
// backlog for a subscriber that wasn't listening yet).
func waitForSubscriber(t *testing.T, ctx context.Context, client *redis.Client) {
	t.Helper()
	require.Eventually(t, func() bool {
		counts, err := client.PubSubNumSub(ctx, queue.EventsChannel).Result()
		return err == nil && counts[queue.EventsChannel] > 0
	}, 3*time.Second, 20*time.Millisecond, "relay never subscribed to the events channel")
}

type recordedCall struct {
	kind       string
	analysisID uuid.UUID
	name       string
	index      int
	total      int
	position   int
	errorCode  string
	message    string
}

type recordingSink struct {
	mu    sync.Mutex
	calls []recordedCall
}

func (s *recordingSink) BroadcastStage(analysisID uuid.UUID, name string, index, total int) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.calls = append(s.calls, recordedCall{kind: "stage", analysisID: analysisID, name: name, index: index, total: total})
}

func (s *recordingSink) BroadcastDone(analysisID uuid.UUID) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.calls = append(s.calls, recordedCall{kind: "done", analysisID: analysisID})
}

func (s *recordingSink) BroadcastFailed(analysisID uuid.UUID, errorCode, message string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.calls = append(s.calls, recordedCall{kind: "failed", analysisID: analysisID, errorCode: errorCode, message: message})
}

func (s *recordingSink) BroadcastPositions(positions map[uuid.UUID]int) {
	s.mu.Lock()
	defer s.mu.Unlock()
	for id, pos := range positions {
		s.calls = append(s.calls, recordedCall{kind: "queued", analysisID: id, position: pos})
	}
}

func (s *recordingSink) snapshot() []recordedCall {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]recordedCall, len(s.calls))
	copy(out, s.calls)
	return out
}

func TestRelayEvents_DecodesAndDispatchesToSink(t *testing.T) {
	client := setupRedis(t)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	sink := &recordingSink{}
	relayErr := make(chan error, 1)
	go func() { relayErr <- queue.RelayEvents(ctx, client, sink, testLogger()) }()

	waitForSubscriber(t, ctx, client)

	analysisID := uuid.New()
	require.NoError(t, client.Publish(ctx, queue.EventsChannel,
		`{"analysis_id":"`+analysisID.String()+`","type":"stage","name":"pitch","index":5,"total":10}`).Err())
	require.NoError(t, client.Publish(ctx, queue.EventsChannel,
		`not valid json at all`).Err()) // must be skipped, not fatal (a bad message can't stop future relays)
	require.NoError(t, client.Publish(ctx, queue.EventsChannel,
		`{"analysis_id":"`+analysisID.String()+`","type":"done"}`).Err())
	require.NoError(t, client.Publish(ctx, queue.EventsChannel,
		`{"analysis_id":"`+analysisID.String()+`","type":"failed","error_code":"NO_VOICE_DETECTED","message":"no voice"}`).Err())
	require.NoError(t, client.Publish(ctx, queue.EventsChannel,
		`{"analysis_id":"`+analysisID.String()+`","type":"queued","position":2}`).Err())

	require.Eventually(t, func() bool {
		return len(sink.snapshot()) == 4
	}, 3*time.Second, 20*time.Millisecond, "expected exactly the 4 valid messages to be dispatched")

	calls := sink.snapshot()
	require.Equal(t, recordedCall{kind: "stage", analysisID: analysisID, name: "pitch", index: 5, total: 10}, calls[0])
	require.Equal(t, recordedCall{kind: "done", analysisID: analysisID}, calls[1])
	require.Equal(t, recordedCall{kind: "failed", analysisID: analysisID, errorCode: "NO_VOICE_DETECTED", message: "no voice"}, calls[2])
	require.Equal(t, recordedCall{kind: "queued", analysisID: analysisID, position: 2}, calls[3])

	cancel()
	require.NoError(t, <-relayErr)
}
