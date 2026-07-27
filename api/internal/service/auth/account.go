package auth

import (
	"context"
	"fmt"

	"github.com/google/uuid"

	"ai-vocal-coach/api/internal/domain"
)

// GetProfile returns the caller's own account.
func (s *Service) GetProfile(ctx context.Context, userID uuid.UUID) (*domain.User, error) {
	return s.users.GetByID(ctx, userID)
}

// DeleteAccount permanently removes the account and every session tied to it
// (FR-07). The database's ON DELETE CASCADE takes analyses and
// progress_snapshots with it.
func (s *Service) DeleteAccount(ctx context.Context, userID uuid.UUID) error {
	if err := s.tokens.RevokeAllForUser(ctx, userID); err != nil {
		return fmt.Errorf("revoke sessions: %w", err)
	}
	if err := s.users.HardDelete(ctx, userID); err != nil {
		return fmt.Errorf("delete account: %w", err)
	}
	return nil
}
