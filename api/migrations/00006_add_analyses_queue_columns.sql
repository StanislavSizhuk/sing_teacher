-- Queue ordering for analyses: a monotonic FIFO sequence and the Redis Streams
-- entry id, so position can be recomputed from Postgres and a canceled job's
-- stream entry can be precisely removed (ADR-0002, ADR-0008, spec 10).
-- +goose Up
ALTER TABLE analyses ADD COLUMN queue_seq BIGSERIAL;
ALTER TABLE analyses ADD COLUMN queue_stream_id TEXT;

-- Position of a queued job = count of queued rows with a lower queue_seq;
-- this partial index keeps that count cheap as the queue grows.
CREATE INDEX analyses_queue_seq_idx ON analyses (queue_seq) WHERE status = 'queued';

-- +goose Down
DROP INDEX IF EXISTS analyses_queue_seq_idx;
ALTER TABLE analyses DROP COLUMN IF EXISTS queue_stream_id;
ALTER TABLE analyses DROP COLUMN IF EXISTS queue_seq;
