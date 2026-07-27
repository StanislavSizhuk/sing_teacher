-- Denormalized overall_score points for the progress-over-time chart (spec 7, FR-35).
-- +goose Up
CREATE TABLE progress_snapshots (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    analysis_id    UUID NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
    overall_score  NUMERIC NOT NULL CHECK (overall_score BETWEEN 0 AND 100),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX progress_snapshots_user_created_idx ON progress_snapshots (user_id, created_at);

-- +goose Down
DROP TABLE IF EXISTS progress_snapshots;
