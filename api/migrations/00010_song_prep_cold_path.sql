-- Cold/warm split (spec 6.2, 10, M2): reference preparation (Demucs,
-- Whisper, reference pitch curve) becomes its own async job on the
-- songs:prep queue instead of running inline on an analysis's first run.
-- vocal_stem_processed's single boolean is replaced by a proper state
-- machine (prep_status/prep_stage/prep_error_code) so the API can report
-- progress (FR-14) and the worker can resume a crashed prep job from its
-- first unfinished stage (spec 6.1, 6.8), same as analyses.stages_json
-- already does.
-- +goose Up
ALTER TABLE songs ADD COLUMN prep_status TEXT NOT NULL DEFAULT 'pending'
    CHECK (prep_status IN ('pending', 'processing', 'ready', 'failed'));
ALTER TABLE songs ADD COLUMN prep_stage TEXT;
ALTER TABLE songs ADD COLUMN prep_error_code TEXT;
-- Per-stage duration_ms already lives inside each StageResult in
-- prep_stages_json (NFR-15) -- no separate durations column, same as
-- analyses has no stage_durations_json distinct from stages_json.
ALTER TABLE songs ADD COLUMN prep_stages_json JSONB;
ALTER TABLE songs ADD COLUMN vocal_stem_path TEXT;
ALTER TABLE songs ADD COLUMN lyrics_available BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE songs ADD COLUMN prepared_at TIMESTAMPTZ;

UPDATE songs SET
    prep_status = CASE WHEN vocal_stem_processed THEN 'ready' ELSE 'pending' END,
    prepared_at = CASE WHEN vocal_stem_processed THEN created_at END,
    lyrics_available = (lyrics_json IS NOT NULL);

ALTER TABLE songs DROP COLUMN vocal_stem_processed;

-- Cold-path monitoring (spec 7.1): only pending/processing rows matter here.
CREATE INDEX songs_prep_status_active_idx ON songs (prep_status)
    WHERE prep_status IN ('pending', 'processing');

-- An analysis queued for a song whose reference isn't ready yet waits here
-- (FR-16) instead of being rejected; woken to 'queued' once songs:prep
-- finishes (spec 10.3).
ALTER TABLE analyses DROP CONSTRAINT analyses_status_check;
ALTER TABLE analyses ADD CONSTRAINT analyses_status_check
    CHECK (status IN ('queued', 'waiting_for_reference', 'processing', 'done', 'failed', 'canceled'));

-- Finds every analysis to wake once a song's prep_status flips to ready/failed (spec 7.1, 10.3).
CREATE INDEX analyses_waiting_for_reference_idx ON analyses (song_id)
    WHERE status = 'waiting_for_reference';

-- +goose Down
DROP INDEX IF EXISTS analyses_waiting_for_reference_idx;
ALTER TABLE analyses DROP CONSTRAINT analyses_status_check;
ALTER TABLE analyses ADD CONSTRAINT analyses_status_check
    CHECK (status IN ('queued', 'processing', 'done', 'failed', 'canceled'));

DROP INDEX IF EXISTS songs_prep_status_active_idx;

ALTER TABLE songs ADD COLUMN vocal_stem_processed BOOLEAN NOT NULL DEFAULT false;
UPDATE songs SET vocal_stem_processed = (prep_status = 'ready');

ALTER TABLE songs DROP COLUMN IF EXISTS prepared_at;
ALTER TABLE songs DROP COLUMN IF EXISTS lyrics_available;
ALTER TABLE songs DROP COLUMN IF EXISTS vocal_stem_path;
ALTER TABLE songs DROP COLUMN IF EXISTS prep_stages_json;
ALTER TABLE songs DROP COLUMN IF EXISTS prep_error_code;
ALTER TABLE songs DROP COLUMN IF EXISTS prep_stage;
ALTER TABLE songs DROP COLUMN IF EXISTS prep_status;
