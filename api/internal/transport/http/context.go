// Package httptransport is the HTTP boundary: chi router, middleware, DTOs
// and handlers. It decodes requests, calls the service layer, and encodes
// responses -- no business logic lives here (spec 12.2).
package httptransport

import (
	"context"

	"github.com/google/uuid"
)

type ctxKey int

const (
	requestIDKey ctxKey = iota
	userIDKey
)

func withRequestID(ctx context.Context, id string) context.Context {
	return context.WithValue(ctx, requestIDKey, id)
}

// requestIDFromContext returns the current request's id, or "" if unset.
func requestIDFromContext(ctx context.Context) string {
	id, _ := ctx.Value(requestIDKey).(string)
	return id
}

func withUserID(ctx context.Context, id uuid.UUID) context.Context {
	return context.WithValue(ctx, userIDKey, id)
}

// userIDFromContext returns the authenticated caller's id. ok is false when
// the request never passed the auth middleware.
func userIDFromContext(ctx context.Context) (uuid.UUID, bool) {
	id, ok := ctx.Value(userIDKey).(uuid.UUID)
	return id, ok
}
