package httptransport

import (
	"context"
	"log/slog"
	"net/http"

	"github.com/google/uuid"

	"ai-vocal-coach/api/internal/domain"
)

// ProgressLister is what ProgressHandler needs from the progress service.
type ProgressLister interface {
	ListByUser(ctx context.Context, userID uuid.UUID) ([]domain.ProgressPoint, error)
}

// ProgressHandler serves /api/v1/progress.
type ProgressHandler struct {
	svc    ProgressLister
	logger *slog.Logger
}

// NewProgressHandler builds a ProgressHandler.
func NewProgressHandler(svc ProgressLister, logger *slog.Logger) *ProgressHandler {
	return &ProgressHandler{svc: svc, logger: logger}
}

// List handles GET /progress: the caller's own overall_score points, oldest
// first, for the FR-35 progress chart.
func (h *ProgressHandler) List(w http.ResponseWriter, r *http.Request) {
	userID, ok := userIDFromContext(r.Context())
	if !ok {
		writeServiceError(h.logger, w, r, domain.ErrInvalidAccessToken)
		return
	}

	points, err := h.svc.ListByUser(r.Context(), userID)
	if err != nil {
		writeServiceError(h.logger, w, r, err)
		return
	}

	resp := make([]progressPointResponse, len(points))
	for i, p := range points {
		resp[i] = newProgressPointResponse(p)
	}
	writeJSON(w, http.StatusOK, resp)
}
