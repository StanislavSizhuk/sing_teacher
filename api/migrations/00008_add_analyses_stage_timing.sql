-- Per-stage timing visibility (spec 6.1, 8.3): a stage change was only ever
-- exposed as a bare name, with no timestamp or position, so a multi-minute
-- stage in flight (Demucs/Whisper on a song's first analysis) looked
-- indistinguishable from a stuck job.
-- +goose Up
ALTER TABLE analyses ADD COLUMN current_stage_started_at TIMESTAMPTZ;
ALTER TABLE analyses ADD COLUMN current_stage_index INTEGER;
ALTER TABLE analyses ADD COLUMN total_stages INTEGER;

-- +goose Down
ALTER TABLE analyses DROP COLUMN IF EXISTS total_stages;
ALTER TABLE analyses DROP COLUMN IF EXISTS current_stage_index;
ALTER TABLE analyses DROP COLUMN IF EXISTS current_stage_started_at;
