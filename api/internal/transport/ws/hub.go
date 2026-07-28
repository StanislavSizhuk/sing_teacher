// Package ws implements the WebSocket status channel for analysis queue
// position updates (spec 8.3). It knows nothing about business rules: it
// only authenticates the connection (via the same access-token parser
// transport/http uses) and relays position numbers the analysis service
// computes.
package ws

import (
	"sync"

	"github.com/google/uuid"
)

// event is the JSON payload pushed to a subscriber (spec 8.3: type, payload).
type event struct {
	Type       string `json:"type"`
	Position   *int   `json:"position,omitempty"`
	Name       string `json:"name,omitempty"`
	Index      int    `json:"index,omitempty"`
	Total      int    `json:"total,omitempty"`
	AnalysisID string `json:"analysis_id,omitempty"`
	ErrorCode  string `json:"error_code,omitempty"`
	Message    string `json:"message,omitempty"`
}

// Hub fans out queue-position updates to every client currently watching an
// analysis. One Hub serves every connection for the process's lifetime.
type Hub struct {
	mu      sync.RWMutex
	clients map[uuid.UUID]map[*client]struct{}
}

// NewHub builds an empty Hub.
func NewHub() *Hub {
	return &Hub{clients: make(map[uuid.UUID]map[*client]struct{})}
}

func (h *Hub) register(analysisID uuid.UUID, c *client) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.clients[analysisID] == nil {
		h.clients[analysisID] = make(map[*client]struct{})
	}
	h.clients[analysisID][c] = struct{}{}
}

func (h *Hub) unregister(analysisID uuid.UUID, c *client) {
	h.mu.Lock()
	defer h.mu.Unlock()
	set := h.clients[analysisID]
	delete(set, c)
	if len(set) == 0 {
		delete(h.clients, analysisID)
	}
}

// BroadcastPositions pushes a `queued` event with the new position to every
// client currently watching each affected analysis (spec 10, FR-23). Called
// by the analyses HTTP handler after a service call that returns a
// changed-positions map (Enqueue, Cancel).
func (h *Hub) BroadcastPositions(positions map[uuid.UUID]int) {
	h.mu.RLock()
	defer h.mu.RUnlock()
	for id, pos := range positions {
		p := pos
		for c := range h.clients[id] {
			c.send(event{Type: "queued", Position: &p})
		}
	}
}

// BroadcastStage pushes a `stage` event: the E3 worker just started stage
// number index of total (1-based), named name (spec 8.3). Called by the
// Redis pub/sub relay (ADR-0010), never by an HTTP handler -- the worker
// runs in a separate process from the API.
func (h *Hub) BroadcastStage(analysisID uuid.UUID, name string, index, total int) {
	h.broadcast(analysisID, event{Type: "stage", Name: name, Index: index, Total: total})
}

// BroadcastDone pushes a `done` event once the worker finishes every stage
// successfully (spec 8.3). The client still re-reads the final result over
// REST; this is only the "go fetch it now" signal.
func (h *Hub) BroadcastDone(analysisID uuid.UUID) {
	h.broadcast(analysisID, event{Type: "done", AnalysisID: analysisID.String()})
}

// BroadcastFailed pushes a `failed` event with the machine-readable error
// code and a human-readable message (spec 8.3, 6.8).
func (h *Hub) BroadcastFailed(analysisID uuid.UUID, errorCode, message string) {
	h.broadcast(analysisID, event{Type: "failed", ErrorCode: errorCode, Message: message})
}

func (h *Hub) broadcast(analysisID uuid.UUID, e event) {
	h.mu.RLock()
	defer h.mu.RUnlock()
	for c := range h.clients[analysisID] {
		c.send(e)
	}
}
