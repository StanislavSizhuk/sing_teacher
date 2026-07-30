-- Dense numeric curves as bytea float32, not JSONB (spec 7.3): a 3-minute
-- pitch curve at a 10ms hop is ~18,000 points -- hundreds of KB of JSON text
-- to store and parse where a packed float32 array is ~4x more compact and
-- needs no parsing. songs.reference_pitch_json is replaced outright (no
-- production data depends on its old shape yet); analyses.user_pitch is new.
-- +goose Up
ALTER TABLE songs RENAME COLUMN reference_pitch_json TO reference_pitch;
ALTER TABLE songs ALTER COLUMN reference_pitch TYPE BYTEA USING NULL;
ALTER TABLE songs ADD COLUMN reference_pitch_meta JSONB;

ALTER TABLE analyses ADD COLUMN user_pitch BYTEA;
ALTER TABLE analyses ADD COLUMN user_pitch_meta JSONB;

-- +goose Down
ALTER TABLE analyses DROP COLUMN IF EXISTS user_pitch_meta;
ALTER TABLE analyses DROP COLUMN IF EXISTS user_pitch;

ALTER TABLE songs DROP COLUMN IF EXISTS reference_pitch_meta;
ALTER TABLE songs ALTER COLUMN reference_pitch TYPE JSONB USING NULL;
ALTER TABLE songs RENAME COLUMN reference_pitch TO reference_pitch_json;
