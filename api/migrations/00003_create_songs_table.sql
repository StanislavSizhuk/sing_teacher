-- Reference song catalog, deduplicated by content hash so a stem is only ever processed once (spec 6.6, 7).
-- +goose Up
CREATE TABLE songs (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_type            TEXT NOT NULL CHECK (source_type IN ('upload', 'youtube')),
    source_url             TEXT,
    content_hash           TEXT NOT NULL,
    title                  TEXT NOT NULL,
    artist                 TEXT,
    duration_sec           INT NOT NULL CHECK (duration_sec > 0),
    lyrics_json            JSONB,
    reference_pitch_json   JSONB,
    vocal_stem_processed   BOOLEAN NOT NULL DEFAULT false,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX songs_content_hash_uidx ON songs (content_hash);

-- +goose Down
DROP TABLE IF EXISTS songs;
