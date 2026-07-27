// Package domain holds entities and sentinel errors. It knows nothing about
// HTTP, SQL or Redis — service and repository depend on it, never the reverse.
package domain

import (
	"time"

	"github.com/google/uuid"
)

// User is an account authenticated by email+password, Google, or both.
type User struct {
	ID                    uuid.UUID
	Email                 string
	PasswordHash          *string // nil when the account only has Google sign-in
	GoogleID              *string // nil when the account has never linked Google
	DisplayName           string
	EmailVerified         bool
	VerificationCodeHash  *string
	VerificationExpiresAt *time.Time
	VerificationAttempts  int
	CreatedAt             time.Time
	DeletedAt             *time.Time
}

// IsDeleted reports whether the account has been soft-deleted (spec 7: unverified
// accounts past their verification deadline are soft-deleted by a background job).
func (u *User) IsDeleted() bool {
	return u.DeletedAt != nil
}
