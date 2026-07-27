// Package queue implements the Redis Streams job queue producer (ADR-0002):
// XADD to publish a job, XLEN to check for overflow, XDEL to remove a
// canceled job's entry. The (future, stage E3) worker owns the consumer
// side (XREADGROUP/XACK/XAUTOCLAIM).
package queue

import (
	"context"
	"fmt"
	"strings"

	"github.com/google/uuid"
	"github.com/redis/go-redis/v9"
)

// StreamName and GroupName are fixed, not configurable: they are an
// implementation detail of how the API and the E3 worker talk to each
// other, not an operator-facing setting.
const (
	StreamName = "analyses:queue"
	GroupName  = "analyses:workers"
)

// Producer publishes analysis jobs onto the Redis Streams queue.
type Producer struct {
	client *redis.Client
}

// NewProducer builds a Producer.
func NewProducer(client *redis.Client) *Producer {
	return &Producer{client: client}
}

// EnsureGroup creates the worker consumer group if it does not already
// exist. Idempotent; called once at startup so the group exists before the
// first job is ever published.
func (p *Producer) EnsureGroup(ctx context.Context) error {
	err := p.client.XGroupCreateMkStream(ctx, StreamName, GroupName, "$").Err()
	if err != nil && !strings.Contains(err.Error(), "BUSYGROUP") {
		return fmt.Errorf("create consumer group: %w", err)
	}
	return nil
}

// Length reports the current queue depth via XLEN, used for the overflow
// check (spec 10, FR-24: 429 once the queue is at QUEUE_MAX_LENGTH).
func (p *Producer) Length(ctx context.Context) (int64, error) {
	n, err := p.client.XLen(ctx, StreamName).Result()
	if err != nil {
		return 0, fmt.Errorf("read queue length: %w", err)
	}
	return n, nil
}

// Enqueue publishes analysisID as a new stream entry and returns its entry
// id (spec 10.1: job_id = analysis_id makes redelivery idempotent).
func (p *Producer) Enqueue(ctx context.Context, analysisID uuid.UUID) (string, error) {
	id, err := p.client.XAdd(ctx, &redis.XAddArgs{
		Stream: StreamName,
		Values: map[string]any{"job_id": analysisID.String()},
	}).Result()
	if err != nil {
		return "", fmt.Errorf("enqueue analysis job: %w", err)
	}
	return id, nil
}

// Remove deletes a specific stream entry, used when a queued analysis is
// canceled before any worker claims it. Best-effort: Postgres's status
// column is the source of truth regardless of whether this succeeds.
func (p *Producer) Remove(ctx context.Context, entryID string) error {
	if err := p.client.XDel(ctx, StreamName, entryID).Err(); err != nil {
		return fmt.Errorf("remove queue entry: %w", err)
	}
	return nil
}
