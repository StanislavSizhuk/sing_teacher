// Package queue implements the Redis Streams job queue producer (ADR-0002):
// XADD to publish a job, XLEN to check for overflow, XDEL to remove a
// canceled job's entry, on either of spec 10.1's two streams
// (analyses:run, songs:prep). The Python worker owns the consumer side
// (XREADGROUP/XACK/XAUTOCLAIM).
package queue

import (
	"context"
	"fmt"
	"strings"

	"github.com/google/uuid"
	"github.com/redis/go-redis/v9"
)

// AnalysesStreamName and AnalysesGroupName are the warm path's stream/group
// (spec 10.1's `analyses:run`): analysis jobs, published by
// analysis.Service.Enqueue/Retry, consumed by the worker's
// AnalysisJobHandler. Fixed, not configurable -- an implementation detail
// of how the API and the worker talk to each other, not an operator setting.
const (
	AnalysesStreamName = "analyses:run"
	AnalysesGroupName  = "analyses:workers"
)

// SongsPrepStreamName and SongsPrepGroupName are the cold path's stream/group
// (spec 10.1's `songs:prep`, M2): reference preparation jobs, published by
// song.Service on a new song (FR-15), consumed by the worker's
// SongPrepJobHandler.
const (
	SongsPrepStreamName = "songs:prep"
	SongsPrepGroupName  = "songs:prep:workers"
)

// Producer publishes jobs onto one Redis Streams queue. The API builds one
// instance per stream (analyses:run, songs:prep) -- spec 10.1's two queues
// share identical admission/publish mechanics, only the stream/group name
// differs, so that is the one thing parameterized here rather than
// duplicating this whole type per stream.
type Producer struct {
	client     *redis.Client
	streamName string
	groupName  string
}

// NewProducer builds a Producer publishing onto streamName, with a consumer
// group named groupName.
func NewProducer(client *redis.Client, streamName, groupName string) *Producer {
	return &Producer{client: client, streamName: streamName, groupName: groupName}
}

// EnsureGroup creates the worker consumer group if it does not already
// exist. Idempotent; called once at startup so the group exists before the
// first job is ever published.
func (p *Producer) EnsureGroup(ctx context.Context) error {
	err := p.client.XGroupCreateMkStream(ctx, p.streamName, p.groupName, "$").Err()
	if err != nil && !strings.Contains(err.Error(), "BUSYGROUP") {
		return fmt.Errorf("create consumer group: %w", err)
	}
	return nil
}

// Length reports the current queue depth via XLEN, used for the cheap,
// best-effort overflow pre-check that runs before any expensive work (spec
// 10, FR-24: 429 once the queue is at QUEUE_MAX_LENGTH). It is not the
// authoritative admission check -- concurrent callers can all pass this
// read before any of them publish, which is exactly the race
// EnqueueIfUnderLimit closes at the point a job is actually admitted.
func (p *Producer) Length(ctx context.Context) (int64, error) {
	n, err := p.client.XLen(ctx, p.streamName).Result()
	if err != nil {
		return 0, fmt.Errorf("read queue length: %w", err)
	}
	return n, nil
}

// Enqueue publishes jobID as a new stream entry and returns its entry id
// (spec 10.1: job_id = analysis_id or song_id makes redelivery idempotent).
// It does not enforce the queue-length cap -- callers on the admission path
// (analysis.Service.Enqueue, song.Service.AddFromUpload/AddFromYouTube) must
// use EnqueueIfUnderLimit instead; this method stays as-is for Retry/
// RetryPrep, whose own row already exists and is not created fresh by this call.
func (p *Producer) Enqueue(ctx context.Context, jobID uuid.UUID) (string, error) {
	id, err := p.client.XAdd(ctx, &redis.XAddArgs{
		Stream: p.streamName,
		Values: map[string]any{"job_id": jobID.String()},
	}).Result()
	if err != nil {
		return "", fmt.Errorf("enqueue job: %w", err)
	}
	return id, nil
}

// enqueueIfUnderLimitScript admits a job only if the stream is still under
// maxLen, in one atomic Redis operation. A separate XLEN-then-XADD would let
// concurrent callers all read the same pre-admission length and all decide
// to publish, overshooting the cap by as many requests as arrived in the
// race window (spec 10, FR-24: 20 concurrent submissions must not leave the
// queue longer than QUEUE_MAX_LENGTH). Redis executes EVAL scripts
// single-threaded relative to every other command, so this check-and-add
// cannot interleave with another caller's.
var enqueueIfUnderLimitScript = redis.NewScript(`
local len = redis.call('XLEN', KEYS[1])
if len >= tonumber(ARGV[1]) then
  return {0, ''}
end
local id = redis.call('XADD', KEYS[1], '*', 'job_id', ARGV[2])
return {1, id}
`)

// EnqueueIfUnderLimit atomically admits jobID onto the queue only if doing
// so keeps the queue length under maxLen. ok is false (with an empty
// streamEntryID) when the queue was already at maxLen -- the caller must
// treat that the same as domain.ErrQueueFull, including undoing any
// not-yet-published state it created in anticipation of admission.
func (p *Producer) EnqueueIfUnderLimit(ctx context.Context, jobID uuid.UUID, maxLen int64) (streamEntryID string, ok bool, err error) {
	res, err := enqueueIfUnderLimitScript.Run(ctx, p.client, []string{p.streamName}, maxLen, jobID.String()).Slice()
	if err != nil {
		return "", false, fmt.Errorf("enqueue job: %w", err)
	}
	if len(res) != 2 {
		return "", false, fmt.Errorf("enqueue analysis job: unexpected script result %v", res)
	}
	admitted, _ := res[0].(int64)
	if admitted == 0 {
		return "", false, nil
	}
	id, _ := res[1].(string)
	return id, true, nil
}

// Remove deletes a specific stream entry, used when a queued analysis is
// canceled before any worker claims it. Best-effort: Postgres's status
// column is the source of truth regardless of whether this succeeds.
func (p *Producer) Remove(ctx context.Context, entryID string) error {
	if err := p.client.XDel(ctx, p.streamName, entryID).Err(); err != nil {
		return fmt.Errorf("remove queue entry: %w", err)
	}
	return nil
}
