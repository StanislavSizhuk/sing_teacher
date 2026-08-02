package httptransport

import (
	"context"
	"errors"
	"io"
	"log/slog"
	"net/http"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"ai-vocal-coach/api/internal/domain"
)

// AnalysisQueuer is what AnalysisHandler needs from the analysis service.
type AnalysisQueuer interface {
	Enqueue(ctx context.Context, userID, songID uuid.UUID, mode domain.AnalysisMode, allowTransposition bool, locale domain.Locale, recording io.Reader) (a *domain.Analysis, positions map[uuid.UUID]int, err error)
	Cancel(ctx context.Context, id, userID uuid.UUID) (a *domain.Analysis, positions map[uuid.UUID]int, err error)
	Retry(ctx context.Context, id, userID uuid.UUID) (a *domain.Analysis, positions map[uuid.UUID]int, err error)
	GetByID(ctx context.Context, id, userID uuid.UUID) (*domain.Analysis, error)
}

// PositionBroadcaster pushes queue position updates to connected WebSocket
// clients (transport/ws.Hub).
type PositionBroadcaster interface {
	BroadcastPositions(positions map[uuid.UUID]int)
}

// AnalysisHandler serves /api/v1/analyses.
type AnalysisHandler struct {
	svc            AnalysisQueuer
	hub            PositionBroadcaster
	logger         *slog.Logger
	maxUploadBytes int64
}

// NewAnalysisHandler builds an AnalysisHandler.
func NewAnalysisHandler(svc AnalysisQueuer, hub PositionBroadcaster, logger *slog.Logger, maxUploadBytes int64) *AnalysisHandler {
	return &AnalysisHandler{svc: svc, hub: hub, logger: logger, maxUploadBytes: maxUploadBytes}
}

// Create handles POST /analyses: puts a recording in the queue and returns
// its id and position immediately (FR-22).
func (h *AnalysisHandler) Create(w http.ResponseWriter, r *http.Request) {
	userID, ok := userIDFromContext(r.Context())
	if !ok {
		writeServiceError(h.logger, w, r, domain.ErrInvalidAccessToken)
		return
	}

	r.Body = http.MaxBytesReader(w, r.Body, h.maxUploadBytes+multipartOverhead)
	if err := r.ParseMultipartForm(multipartMemoryThreshold); err != nil { // #nosec G120 -- body is already bounded by MaxBytesReader above
		var maxErr *http.MaxBytesError
		if errors.As(err, &maxErr) {
			writeServiceError(h.logger, w, r, domain.ErrAudioTooLarge)
			return
		}
		badRequest(w, r, "request body must be valid multipart form data")
		return
	}
	defer func() {
		if r.MultipartForm != nil {
			_ = r.MultipartForm.RemoveAll()
		}
	}()

	songID, err := uuid.Parse(r.FormValue("song_id"))
	if err != nil {
		badRequest(w, r, "song_id must be a valid uuid")
		return
	}
	mode, err := validateAnalysisMode(r.FormValue("mode"))
	if err != nil {
		badRequest(w, r, err.Error())
		return
	}
	allowTransposition := parseAllowTransposition(r.FormValue("allow_transposition"), mode)
	locale, err := validateLocale(r.FormValue("locale"))
	if err != nil {
		badRequest(w, r, err.Error())
		return
	}
	file, _, err := r.FormFile("recording")
	if err != nil {
		badRequest(w, r, "recording file is required")
		return
	}
	defer func() { _ = file.Close() }()

	result, positions, err := h.svc.Enqueue(r.Context(), userID, songID, mode, allowTransposition, locale, file)
	if err != nil {
		writeServiceError(h.logger, w, r, err)
		return
	}
	h.hub.BroadcastPositions(positions)
	writeJSON(w, http.StatusAccepted, newAnalysisResponse(result))
}

// Get handles GET /analyses/{id}: current status, scoped to its owner.
func (h *AnalysisHandler) Get(w http.ResponseWriter, r *http.Request) {
	userID, ok := userIDFromContext(r.Context())
	if !ok {
		writeServiceError(h.logger, w, r, domain.ErrInvalidAccessToken)
		return
	}
	id, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		badRequest(w, r, "invalid analysis id")
		return
	}
	result, err := h.svc.GetByID(r.Context(), id, userID)
	if err != nil {
		writeServiceError(h.logger, w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, newAnalysisResponse(result))
}

// Cancel handles POST /analyses/{id}/cancel: cancels while still queued (FR-25).
func (h *AnalysisHandler) Cancel(w http.ResponseWriter, r *http.Request) {
	userID, ok := userIDFromContext(r.Context())
	if !ok {
		writeServiceError(h.logger, w, r, domain.ErrInvalidAccessToken)
		return
	}
	id, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		badRequest(w, r, "invalid analysis id")
		return
	}
	result, positions, err := h.svc.Cancel(r.Context(), id, userID)
	if err != nil {
		writeServiceError(h.logger, w, r, err)
		return
	}
	h.hub.BroadcastPositions(positions)
	writeJSON(w, http.StatusOK, newAnalysisResponse(result))
}

// Retry handles POST /analyses/{id}/retry: restarts a failed analysis
// without asking for the recording again (FR-26).
func (h *AnalysisHandler) Retry(w http.ResponseWriter, r *http.Request) {
	userID, ok := userIDFromContext(r.Context())
	if !ok {
		writeServiceError(h.logger, w, r, domain.ErrInvalidAccessToken)
		return
	}
	id, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		badRequest(w, r, "invalid analysis id")
		return
	}
	result, positions, err := h.svc.Retry(r.Context(), id, userID)
	if err != nil {
		writeServiceError(h.logger, w, r, err)
		return
	}
	h.hub.BroadcastPositions(positions)
	writeJSON(w, http.StatusAccepted, newAnalysisResponse(result))
}
