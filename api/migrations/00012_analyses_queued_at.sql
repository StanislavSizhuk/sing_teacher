-- `queued_at`: when the current queued/waiting_for_reference wait began
-- (FR-22's live timer, QueueStatus.tsx). Distinct from `created_at`
-- (immutable, the original submission) because Retry (FR-26) reuses the
-- same row -- without this, a retried analysis's elapsed-wait timer
-- measured from its first-ever submission, not from the retry, and could
-- read as hours even seconds after the user clicked Retry.
-- Backfilled from created_at for existing rows (their real first wait,
-- the best available approximation); Enqueue and Retry both set it to
-- now() going forward. wake_waiting_for_reference (worker, Python) does
-- not touch it -- transitioning out of waiting_for_reference into queued
-- is not a new wait from the user's perspective.
-- +goose Up
ALTER TABLE analyses ADD COLUMN queued_at TIMESTAMPTZ;
UPDATE analyses SET queued_at = created_at;
ALTER TABLE analyses ALTER COLUMN queued_at SET NOT NULL;
ALTER TABLE analyses ALTER COLUMN queued_at SET DEFAULT now();

-- +goose Down
ALTER TABLE analyses DROP COLUMN IF EXISTS queued_at;
