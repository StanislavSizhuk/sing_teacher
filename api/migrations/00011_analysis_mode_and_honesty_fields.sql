-- Mode selection (FR-27) and the data-honesty fields M3's worker already
-- computes but had nowhere to land (spec 6.14, 6.15, 6.16, 7, M4): which
-- mode a user picked and which one actually ran, confidence, machine-readable
-- warnings, why an unavailable aspect is null instead of 0 (FR-41), the
-- weight profile a score was computed under, and the diagnostic signals
-- behind the confidence level. Every new *_score column already tolerates
-- NULL (migration 00004) -- mixed mode simply never writes breath/timbre.
-- +goose Up
ALTER TABLE analyses ADD COLUMN mode TEXT NOT NULL DEFAULT 'clean'
    CHECK (mode IN ('clean', 'mixed'));
ALTER TABLE analyses ADD COLUMN effective_mode TEXT
    CHECK (effective_mode IS NULL OR effective_mode IN ('clean', 'mixed'));
ALTER TABLE analyses ADD COLUMN allow_transposition BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE analyses ADD COLUMN confidence TEXT
    CHECK (confidence IS NULL OR confidence IN ('high', 'medium', 'low'));
ALTER TABLE analyses ADD COLUMN warnings_json JSONB;
ALTER TABLE analyses ADD COLUMN unavailable_aspects_json JSONB;
ALTER TABLE analyses ADD COLUMN aspect_confidence_json JSONB;
ALTER TABLE analyses ADD COLUMN key_shift_semitones NUMERIC;
ALTER TABLE analyses ADD COLUMN accompaniment_level NUMERIC;
ALTER TABLE analyses ADD COLUMN voiced_ratio NUMERIC;
ALTER TABLE analyses ADD COLUMN alignment_cost NUMERIC;
ALTER TABLE analyses ADD COLUMN weights_profile TEXT;

-- Guards against a silent loss of the breath aspect in `clean` (spec 7.1):
-- BreathStage is required=True and mode-agnostic to itself, so a `clean`
-- analysis that reached `done` must have a breath_score. `mixed` never
-- computes breath at all (FR-41), so it is exempt outright.
ALTER TABLE analyses ADD CONSTRAINT analyses_clean_has_breath_score
    CHECK (mode = 'mixed' OR breath_score IS NOT NULL OR status <> 'done');

ALTER TABLE progress_snapshots ADD COLUMN mode TEXT NOT NULL DEFAULT 'clean'
    CHECK (mode IN ('clean', 'mixed'));
ALTER TABLE progress_snapshots ADD COLUMN confidence TEXT
    CHECK (confidence IS NULL OR confidence IN ('high', 'medium', 'low'));

-- Progress chart grouped/filtered by mode (spec 7.1, FR-49).
CREATE INDEX progress_snapshots_user_mode_created_idx
    ON progress_snapshots (user_id, mode, created_at);

-- +goose Down
DROP INDEX IF EXISTS progress_snapshots_user_mode_created_idx;
ALTER TABLE progress_snapshots DROP COLUMN IF EXISTS confidence;
ALTER TABLE progress_snapshots DROP COLUMN IF EXISTS mode;

ALTER TABLE analyses DROP CONSTRAINT IF EXISTS analyses_clean_has_breath_score;
ALTER TABLE analyses DROP COLUMN IF EXISTS weights_profile;
ALTER TABLE analyses DROP COLUMN IF EXISTS alignment_cost;
ALTER TABLE analyses DROP COLUMN IF EXISTS voiced_ratio;
ALTER TABLE analyses DROP COLUMN IF EXISTS accompaniment_level;
ALTER TABLE analyses DROP COLUMN IF EXISTS key_shift_semitones;
ALTER TABLE analyses DROP COLUMN IF EXISTS aspect_confidence_json;
ALTER TABLE analyses DROP COLUMN IF EXISTS unavailable_aspects_json;
ALTER TABLE analyses DROP COLUMN IF EXISTS warnings_json;
ALTER TABLE analyses DROP COLUMN IF EXISTS confidence;
ALTER TABLE analyses DROP COLUMN IF EXISTS allow_transposition;
ALTER TABLE analyses DROP COLUMN IF EXISTS effective_mode;
ALTER TABLE analyses DROP COLUMN IF EXISTS mode;
