package httptransport

import (
	"net/http"

	"ai-vocal-coach/api/internal/domain"
)

// GetMe handles GET /me: returns the authenticated caller's profile.
func (h *Handler) GetMe(w http.ResponseWriter, r *http.Request) {
	userID, ok := userIDFromContext(r.Context())
	if !ok {
		writeServiceError(h.logger, w, r, domain.ErrInvalidAccessToken)
		return
	}
	user, err := h.svc.GetProfile(r.Context(), userID)
	if err != nil {
		writeServiceError(h.logger, w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, newUserResponse(user))
}

// DeleteMe handles DELETE /me: permanently deletes the caller's own account.
func (h *Handler) DeleteMe(w http.ResponseWriter, r *http.Request) {
	userID, ok := userIDFromContext(r.Context())
	if !ok {
		writeServiceError(h.logger, w, r, domain.ErrInvalidAccessToken)
		return
	}
	if err := h.svc.DeleteAccount(r.Context(), userID); err != nil {
		writeServiceError(h.logger, w, r, err)
		return
	}
	h.clearRefreshCookie(w)
	w.WriteHeader(http.StatusNoContent)
}
