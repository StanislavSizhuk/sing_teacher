package ws

import (
	"context"
	"net/http"
	"time"

	"github.com/coder/websocket"
	"github.com/coder/websocket/wsjson"
	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"ai-vocal-coach/api/internal/domain"
)

// authTimeout bounds how long a client has to send its auth message after
// connecting, so an idle handshake never holds a connection open forever.
const authTimeout = 10 * time.Second

// AccessTokenParser validates the access token sent as the connection's
// first message (spec 8.3: authentication rides the first message after
// connect, never a query parameter, so it never lands in proxy logs).
type AccessTokenParser interface {
	Parse(token string) (uuid.UUID, error)
}

// AnalysisReader is the narrow read this handler needs: confirming the
// analysis exists and belongs to the caller, and reporting its position at
// connect time.
type AnalysisReader interface {
	GetByID(ctx context.Context, id, userID uuid.UUID) (*domain.Analysis, error)
}

// Handler upgrades and serves GET /api/v1/ws/analyses/{id}.
type Handler struct {
	hub        *Hub
	analyses   AnalysisReader
	tokens     AccessTokenParser
	corsOrigin string
}

// NewHandler builds a Handler. corsOrigin is the single allowed WebSocket
// origin, matching CORS_ALLOWED_ORIGIN (spec 11.2).
func NewHandler(hub *Hub, analyses AnalysisReader, tokens AccessTokenParser, corsOrigin string) *Handler {
	return &Handler{hub: hub, analyses: analyses, tokens: tokens, corsOrigin: corsOrigin}
}

type authMessage struct {
	Token string `json:"token"`
}

// ServeAnalysis upgrades the connection, authenticates it via the first
// message, verifies the caller owns the analysis, and then relays queue
// position updates until the client disconnects.
func (h *Handler) ServeAnalysis(w http.ResponseWriter, r *http.Request) {
	id, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		http.Error(w, "invalid analysis id", http.StatusBadRequest)
		return
	}

	conn, err := websocket.Accept(w, r, &websocket.AcceptOptions{
		OriginPatterns: []string{h.corsOrigin},
	})
	if err != nil {
		return // Accept already wrote the appropriate HTTP error response
	}
	defer func() { _ = conn.CloseNow() }()

	authCtx, cancel := context.WithTimeout(r.Context(), authTimeout)
	var auth authMessage
	err = wsjson.Read(authCtx, conn, &auth)
	cancel()
	if err != nil {
		_ = conn.Close(websocket.StatusPolicyViolation, "authentication required")
		return
	}

	userID, err := h.tokens.Parse(auth.Token)
	if err != nil {
		_ = conn.Close(websocket.StatusPolicyViolation, "invalid access token")
		return
	}

	current, err := h.analyses.GetByID(r.Context(), id, userID)
	if err != nil {
		_ = conn.Close(websocket.StatusPolicyViolation, "analysis not found")
		return
	}

	c := newClient(conn)
	h.hub.register(id, c)
	defer h.hub.unregister(id, c)

	if current.Status == domain.AnalysisStatusQueued && current.QueuePosition != nil {
		c.send(event{Type: "queued", Position: current.QueuePosition})
	}

	runCtx, runCancel := context.WithCancel(r.Context())
	defer runCancel()
	go c.run(runCtx)

	// The client sends nothing further after auth (spec 8.3: WS is a
	// status-only transport). Block here purely to detect disconnection --
	// coder/websocket also needs an active reader to process control frames
	// (pong replies) for c.run's pings to observe.
	for {
		if _, _, err := conn.Read(r.Context()); err != nil {
			return
		}
	}
}
