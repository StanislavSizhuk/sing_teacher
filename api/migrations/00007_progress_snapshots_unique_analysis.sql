-- One progress point per analysis: lets the worker upsert on retry instead of duplicating a chart point (FR-35, spec 6.8).
-- +goose Up
ALTER TABLE progress_snapshots ADD CONSTRAINT progress_snapshots_analysis_id_key UNIQUE (analysis_id);

-- +goose Down
ALTER TABLE progress_snapshots DROP CONSTRAINT IF EXISTS progress_snapshots_analysis_id_key;
