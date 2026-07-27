package auth

import (
	"context"
	"fmt"

	"github.com/google/uuid"
)

// Refresh rotates a refresh token and issues a new access token. Errors
// (invalid, expired, or reused) are returned unwrapped so transport can map
// domain.ErrRefreshTokenReused to a hard logout distinctly from a plain 401.
func (s *Service) Refresh(ctx context.Context, refreshToken string) (*Session, error) {
	newToken, userID, err := s.tokens.Rotate(ctx, refreshToken)
	if err != nil {
		return nil, err
	}
	accessToken, err := s.access.Issue(userID)
	if err != nil {
		return nil, fmt.Errorf("issue access token: %w", err)
	}
	return &Session{AccessToken: accessToken, RefreshToken: newToken}, nil
}

// Logout revokes a single refresh token (the current session only).
func (s *Service) Logout(ctx context.Context, refreshToken string) error {
	return s.tokens.Revoke(ctx, refreshToken)
}

// LogoutAll revokes every refresh token family belonging to userID (FR-06:
// sign out of all devices).
func (s *Service) LogoutAll(ctx context.Context, userID uuid.UUID) error {
	return s.tokens.RevokeAllForUser(ctx, userID)
}
