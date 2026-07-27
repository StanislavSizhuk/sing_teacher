-- Account table: email+password or Google identity, plus email verification state (spec 7, 9).
-- +goose Up
CREATE TABLE users (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email                    CITEXT NOT NULL,
    password_hash            TEXT,
    google_id                TEXT,
    display_name             TEXT NOT NULL,
    email_verified           BOOLEAN NOT NULL DEFAULT false,
    verification_code_hash   TEXT,
    verification_expires_at  TIMESTAMPTZ,
    verification_attempts    INT NOT NULL DEFAULT 0,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at               TIMESTAMPTZ,
    CONSTRAINT users_password_or_google_chk CHECK (password_hash IS NOT NULL OR google_id IS NOT NULL)
);

-- Partial: a soft-deleted account frees its email for reuse (7.1).
CREATE UNIQUE INDEX users_email_active_uidx ON users (email) WHERE deleted_at IS NULL;
CREATE UNIQUE INDEX users_google_id_uidx ON users (google_id) WHERE google_id IS NOT NULL;

-- +goose Down
DROP TABLE IF EXISTS users;
