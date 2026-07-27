// Package postgres implements the repository interfaces declared by the
// service layer against PostgreSQL, using pgx.
package postgres

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"

	"ai-vocal-coach/api/internal/domain"
)

// uniqueViolation is the Postgres SQLSTATE for a unique-constraint conflict.
const uniqueViolation = "23505"

// UserRepository persists domain.User rows in Postgres.
type UserRepository struct {
	pool *pgxpool.Pool
}

// NewUserRepository builds a UserRepository backed by the given pool.
func NewUserRepository(pool *pgxpool.Pool) *UserRepository {
	return &UserRepository{pool: pool}
}

const userColumns = `id, email, password_hash, google_id, display_name, email_verified,
	verification_code_hash, verification_expires_at, verification_attempts, created_at, deleted_at`

func scanUser(row pgx.Row) (*domain.User, error) {
	var u domain.User
	err := row.Scan(&u.ID, &u.Email, &u.PasswordHash, &u.GoogleID, &u.DisplayName, &u.EmailVerified,
		&u.VerificationCodeHash, &u.VerificationExpiresAt, &u.VerificationAttempts, &u.CreatedAt, &u.DeletedAt)
	if err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, domain.ErrNotFound
		}
		return nil, fmt.Errorf("scan user: %w", err)
	}
	return &u, nil
}

// Create inserts a new user. It returns domain.ErrEmailTaken if the email is
// already used by an active account.
func (r *UserRepository) Create(ctx context.Context, u *domain.User) error {
	const q = `
		INSERT INTO users (id, email, password_hash, google_id, display_name, email_verified,
			verification_code_hash, verification_expires_at, verification_attempts, created_at)
		VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, now())
		RETURNING created_at`

	err := r.pool.QueryRow(ctx, q, u.ID, u.Email, u.PasswordHash, u.GoogleID, u.DisplayName, u.EmailVerified,
		u.VerificationCodeHash, u.VerificationExpiresAt, u.VerificationAttempts).Scan(&u.CreatedAt)
	if err != nil {
		var pgErr *pgconn.PgError
		if errors.As(err, &pgErr) && pgErr.Code == uniqueViolation {
			return domain.ErrEmailTaken
		}
		return fmt.Errorf("create user: %w", err)
	}
	return nil
}

// GetByEmail returns the active account with this email (case-insensitive), if any.
func (r *UserRepository) GetByEmail(ctx context.Context, email string) (*domain.User, error) {
	q := `SELECT ` + userColumns + ` FROM users WHERE email = $1 AND deleted_at IS NULL`
	return scanUser(r.pool.QueryRow(ctx, q, email))
}

// GetByID returns the active account with this id, if any.
func (r *UserRepository) GetByID(ctx context.Context, id uuid.UUID) (*domain.User, error) {
	q := `SELECT ` + userColumns + ` FROM users WHERE id = $1 AND deleted_at IS NULL`
	return scanUser(r.pool.QueryRow(ctx, q, id))
}

// GetByGoogleID returns the active account linked to this Google subject, if any.
func (r *UserRepository) GetByGoogleID(ctx context.Context, googleID string) (*domain.User, error) {
	q := `SELECT ` + userColumns + ` FROM users WHERE google_id = $1 AND deleted_at IS NULL`
	return scanUser(r.pool.QueryRow(ctx, q, googleID))
}

// UpdateVerificationCode stores a freshly generated verification code hash and
// resets the attempt counter, used at registration and on resend.
func (r *UserRepository) UpdateVerificationCode(ctx context.Context, userID uuid.UUID, codeHash string, expiresAt time.Time) error {
	const q = `UPDATE users SET verification_code_hash = $2, verification_expires_at = $3, verification_attempts = 0 WHERE id = $1`
	ct, err := r.pool.Exec(ctx, q, userID, codeHash, expiresAt)
	if err != nil {
		return fmt.Errorf("update verification code: %w", err)
	}
	if ct.RowsAffected() == 0 {
		return domain.ErrNotFound
	}
	return nil
}

// IncrementVerificationAttempts adds one failed attempt and returns the new total.
func (r *UserRepository) IncrementVerificationAttempts(ctx context.Context, userID uuid.UUID) (int, error) {
	const q = `UPDATE users SET verification_attempts = verification_attempts + 1 WHERE id = $1 RETURNING verification_attempts`
	var attempts int
	if err := r.pool.QueryRow(ctx, q, userID).Scan(&attempts); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return 0, domain.ErrNotFound
		}
		return 0, fmt.Errorf("increment verification attempts: %w", err)
	}
	return attempts, nil
}

// MarkVerified flips email_verified and clears the now-unneeded code fields.
func (r *UserRepository) MarkVerified(ctx context.Context, userID uuid.UUID) error {
	const q = `UPDATE users SET email_verified = true, verification_code_hash = NULL,
		verification_expires_at = NULL, verification_attempts = 0 WHERE id = $1`
	ct, err := r.pool.Exec(ctx, q, userID)
	if err != nil {
		return fmt.Errorf("mark verified: %w", err)
	}
	if ct.RowsAffected() == 0 {
		return domain.ErrNotFound
	}
	return nil
}

// LinkGoogleID attaches a Google subject to an existing email+password account.
func (r *UserRepository) LinkGoogleID(ctx context.Context, userID uuid.UUID, googleID string) error {
	const q = `UPDATE users SET google_id = $2 WHERE id = $1`
	_, err := r.pool.Exec(ctx, q, userID, googleID)
	if err != nil {
		var pgErr *pgconn.PgError
		if errors.As(err, &pgErr) && pgErr.Code == uniqueViolation {
			return domain.ErrEmailTaken // this Google identity is already linked elsewhere
		}
		return fmt.Errorf("link google id: %w", err)
	}
	return nil
}

// HardDelete removes the account outright; ON DELETE CASCADE takes analyses and
// progress_snapshots with it (FR-07).
func (r *UserRepository) HardDelete(ctx context.Context, userID uuid.UUID) error {
	ct, err := r.pool.Exec(ctx, `DELETE FROM users WHERE id = $1`, userID)
	if err != nil {
		return fmt.Errorf("delete user: %w", err)
	}
	if ct.RowsAffected() == 0 {
		return domain.ErrNotFound
	}
	return nil
}

// SoftDeleteExpiredUnverified soft-deletes accounts whose verification code
// expired without the account ever being verified (FR-05), and returns the count.
func (r *UserRepository) SoftDeleteExpiredUnverified(ctx context.Context) (int64, error) {
	const q = `UPDATE users SET deleted_at = now()
		WHERE email_verified = false AND deleted_at IS NULL AND verification_expires_at < now()`
	ct, err := r.pool.Exec(ctx, q)
	if err != nil {
		return 0, fmt.Errorf("soft delete expired unverified users: %w", err)
	}
	return ct.RowsAffected(), nil
}
