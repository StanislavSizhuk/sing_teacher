package auth

import "context"

// CleanupExpiredUnverifiedAccounts soft-deletes accounts whose verification
// code expired without ever being verified (FR-05). It is driven by an hourly
// ticker in cmd/api, not by any HTTP request.
func (s *Service) CleanupExpiredUnverifiedAccounts(ctx context.Context) (int64, error) {
	return s.users.SoftDeleteExpiredUnverified(ctx)
}
