-- One analysis job per (user, song): status, per-aspect scores, and intermediate pipeline state (spec 6.1, 7).
-- +goose Up
CREATE TABLE analyses (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id            UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    song_id            UUID NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
    status             TEXT NOT NULL DEFAULT 'queued'
                       CHECK (status IN ('queued', 'processing', 'done', 'failed', 'canceled')),
    queue_position     INT,
    current_stage      TEXT,
    error_code         TEXT,
    pitch_score        NUMERIC CHECK (pitch_score BETWEEN 0 AND 100),
    rhythm_score       NUMERIC CHECK (rhythm_score BETWEEN 0 AND 100),
    vibrato_score      NUMERIC CHECK (vibrato_score BETWEEN 0 AND 100),
    breath_score       NUMERIC CHECK (breath_score BETWEEN 0 AND 100),
    dynamics_score     NUMERIC CHECK (dynamics_score BETWEEN 0 AND 100),
    timbre_score       NUMERIC CHECK (timbre_score BETWEEN 0 AND 100),
    overall_score      NUMERIC CHECK (overall_score BETWEEN 0 AND 100),
    pitch_curve_json   JSONB,
    stages_json        JSONB,
    feedback_text      TEXT,
    scoring_version    TEXT,
    model_versions     JSONB,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at       TIMESTAMPTZ
);

-- History pagination (7.1).
CREATE INDEX analyses_user_created_idx ON analyses (user_id, created_at DESC);
-- Queue monitoring: only queued/processing rows matter for this index (7.1).
CREATE INDEX analyses_active_status_idx ON analyses (status) WHERE status IN ('queued', 'processing');

-- +goose Down
DROP TABLE IF EXISTS analyses;
