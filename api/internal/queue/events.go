package queue

import (
	"context"
	"encoding/json"
	"log/slog"

	"github.com/google/uuid"
	"github.com/redis/go-redis/v9"
)

// EventsChannel is the Redis Pub/Sub channel the E3 worker publishes
// analysis lifecycle events to (spec 8.3, ADR-0010). Must match the
// worker's queue/events.py CHANNEL_NAME exactly.
const EventsChannel = "analyses:events"

// EventSink receives decoded worker events; implemented by transport/ws.Hub.
// Declared here, on the consumer side (this package), rather than depending
// on the ws package directly.
type EventSink interface {
	BroadcastStage(analysisID uuid.UUID, name string, index, total int)
	BroadcastDone(analysisID uuid.UUID)
	BroadcastFailed(analysisID uuid.UUID, errorCode, message string)
	// BroadcastPositions relays a "queued" event the worker itself
	// published -- specifically, an analysis woken from
	// waiting_for_reference once its song's cold path reached ready (spec
	// 10.3, FR-16). The HTTP-driven Enqueue/Cancel path calls this
	// directly, in-process; this is the worker-initiated equivalent.
	BroadcastPositions(positions map[uuid.UUID]int)
}

// workerEvent mirrors the JSON the worker's queue.events.RedisEventPublisher sends.
type workerEvent struct {
	AnalysisID string `json:"analysis_id"`
	Type       string `json:"type"`
	Name       string `json:"name"`
	Index      int    `json:"index"`
	Total      int    `json:"total"`
	Position   int    `json:"position"`
	ErrorCode  string `json:"error_code"`
	Message    string `json:"message"`
}

// RelayEvents subscribes to EventsChannel and forwards every decoded
// message to sink until ctx is canceled. A malformed message is logged and
// skipped, never fatal: one bad payload must not stop every future WS push
// until the process restarts (spec 8.3's WS channel is best-effort by
// design -- REST is the fallback of record).
func RelayEvents(ctx context.Context, client *redis.Client, sink EventSink, logger *slog.Logger) error {
	sub := client.Subscribe(ctx, EventsChannel)
	defer func() { _ = sub.Close() }()

	ch := sub.Channel()
	for {
		select {
		case <-ctx.Done():
			return nil
		case msg, ok := <-ch:
			if !ok {
				return nil
			}
			relayOne(msg.Payload, sink, logger)
		}
	}
}

func relayOne(payload string, sink EventSink, logger *slog.Logger) {
	var evt workerEvent
	if err := json.Unmarshal([]byte(payload), &evt); err != nil {
		logger.Error("worker event: invalid json", "error", err.Error())
		return
	}
	analysisID, err := uuid.Parse(evt.AnalysisID)
	if err != nil {
		logger.Error("worker event: invalid analysis_id", "error", err.Error())
		return
	}

	switch evt.Type {
	case "stage":
		sink.BroadcastStage(analysisID, evt.Name, evt.Index, evt.Total)
	case "done":
		sink.BroadcastDone(analysisID)
	case "failed":
		sink.BroadcastFailed(analysisID, evt.ErrorCode, evt.Message)
	case "queued":
		sink.BroadcastPositions(map[uuid.UUID]int{analysisID: evt.Position})
	default:
		logger.Error("worker event: unknown type", "type", evt.Type)
	}
}
