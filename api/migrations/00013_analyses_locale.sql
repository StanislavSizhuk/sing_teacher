-- The language (ADR-0031) the FR-32 feedback report is written in: the
-- caller's own choice at POST /analyses, same shape as mode/
-- allow_transposition (migration 00011) -- fixed at creation, never
-- retroactively changed by a later UI language switch.
-- +goose Up
ALTER TABLE analyses ADD COLUMN locale TEXT NOT NULL DEFAULT 'en'
    CHECK (locale IN ('en', 'uk'));

-- +goose Down
ALTER TABLE analyses DROP COLUMN IF EXISTS locale;
