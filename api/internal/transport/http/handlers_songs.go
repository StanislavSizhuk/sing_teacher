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

// multipartOverhead is generous headroom atop the raw audio byte cap for
// multipart form field/boundary bytes, so a compliant upload at exactly the
// limit is never rejected for encoding overhead it didn't create.
const multipartOverhead = 64 * 1024

// multipartMemoryThreshold bounds how much of a multipart request
// ParseMultipartForm buffers in memory; anything beyond it spills to a temp
// file (standard net/http behavior), cleaned up by MultipartForm.RemoveAll.
const multipartMemoryThreshold = 1 << 20

// SongUploader is what SongHandler needs from the song service.
type SongUploader interface {
	AddFromUpload(ctx context.Context, title, artist string, file io.Reader) (result *domain.Song, reused bool, err error)
	AddFromYouTube(ctx context.Context, rawURL, titleOverride string) (result *domain.Song, reused bool, err error)
	GetByID(ctx context.Context, id uuid.UUID) (*domain.Song, error)
	// RetryPrep restarts a song's cold path after it failed (FR-17).
	RetryPrep(ctx context.Context, id uuid.UUID) (*domain.Song, error)
}

// SongHandler serves /api/v1/songs.
type SongHandler struct {
	svc            SongUploader
	logger         *slog.Logger
	maxUploadBytes int64
}

// NewSongHandler builds a SongHandler.
func NewSongHandler(svc SongUploader, logger *slog.Logger, maxUploadBytes int64) *SongHandler {
	return &SongHandler{svc: svc, logger: logger, maxUploadBytes: maxUploadBytes}
}

// parseMultipart bounds the request body and parses it as multipart form
// data, mapping an over-limit body to a clean domain error instead of a raw
// I/O failure (spec 11.3: size enforced at the boundary).
func (h *SongHandler) parseMultipart(w http.ResponseWriter, r *http.Request) error {
	r.Body = http.MaxBytesReader(w, r.Body, h.maxUploadBytes+multipartOverhead)
	if err := r.ParseMultipartForm(multipartMemoryThreshold); err != nil { // #nosec G120 -- body is already bounded by MaxBytesReader above
		var maxErr *http.MaxBytesError
		if errors.As(err, &maxErr) {
			return domain.ErrAudioTooLarge
		}
		return err
	}
	return nil
}

// Create handles POST /songs: adds a song by file upload or YouTube link
// (FR-10, FR-11), multipart field source_type selects which.
func (h *SongHandler) Create(w http.ResponseWriter, r *http.Request) {
	if err := h.parseMultipart(w, r); err != nil {
		if errors.Is(err, domain.ErrAudioTooLarge) {
			writeServiceError(h.logger, w, r, err)
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

	switch r.FormValue("source_type") {
	case "upload":
		h.createFromUpload(w, r)
	case "youtube":
		h.createFromYouTube(w, r)
	default:
		badRequest(w, r, "source_type must be 'upload' or 'youtube'")
	}
}

func (h *SongHandler) createFromUpload(w http.ResponseWriter, r *http.Request) {
	title, err := validateSongTitle(r.FormValue("title"))
	if err != nil {
		badRequest(w, r, err.Error())
		return
	}
	artist, err := validateArtist(r.FormValue("artist"))
	if err != nil {
		badRequest(w, r, err.Error())
		return
	}
	file, _, err := r.FormFile("file")
	if err != nil {
		badRequest(w, r, "file is required for source_type=upload")
		return
	}
	defer func() { _ = file.Close() }()

	result, reused, err := h.svc.AddFromUpload(r.Context(), title, artist, file)
	if err != nil {
		writeServiceError(h.logger, w, r, err)
		return
	}
	writeJSON(w, http.StatusCreated, newSongResponse(result, reused))
}

func (h *SongHandler) createFromYouTube(w http.ResponseWriter, r *http.Request) {
	title, err := validateOptionalTitle(r.FormValue("title"))
	if err != nil {
		badRequest(w, r, err.Error())
		return
	}
	youtubeURL, err := validateYouTubeURLField(r.FormValue("youtube_url"))
	if err != nil {
		badRequest(w, r, err.Error())
		return
	}

	result, reused, err := h.svc.AddFromYouTube(r.Context(), youtubeURL, title)
	if err != nil {
		writeServiceError(h.logger, w, r, err)
		return
	}
	writeJSON(w, http.StatusCreated, newSongResponse(result, reused))
}

// Get handles GET /songs/{id}: reference preparation status (FR-14).
func (h *SongHandler) Get(w http.ResponseWriter, r *http.Request) {
	id, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		badRequest(w, r, "invalid song id")
		return
	}
	result, err := h.svc.GetByID(r.Context(), id)
	if err != nil {
		writeServiceError(h.logger, w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, newSongResponse(result, false))
}

// Prepare handles POST /songs/{id}/prepare: restarts a song's cold path
// after it failed, without asking for the file again (FR-17).
func (h *SongHandler) Prepare(w http.ResponseWriter, r *http.Request) {
	id, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		badRequest(w, r, "invalid song id")
		return
	}
	result, err := h.svc.RetryPrep(r.Context(), id)
	if err != nil {
		writeServiceError(h.logger, w, r, err)
		return
	}
	writeJSON(w, http.StatusAccepted, newSongResponse(result, false))
}
