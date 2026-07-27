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
	Type     string `json:"type"`
	Position *int   `json:"position,omitempty"`
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
