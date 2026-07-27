package ws

import (
	"context"
	"time"

	"github.com/coder/websocket"
	"github.com/coder/websocket/wsjson"
)

// pingInterval matches spec 8.3: "Пінг кожні 30 с".
const pingInterval = 30 * time.Second

// writeTimeout bounds every outbound write (ping or event) so one stalled
// client can never block its goroutine, let alone the broadcaster, indefinitely.
const writeTimeout = 10 * time.Second

// eventBufferSize is how many pending events a slow client tolerates before
// new ones are dropped rather than blocking the broadcaster (position
// updates are idempotent snapshots, not a log -- a dropped one is
// superseded by the next).
const eventBufferSize = 8

// client owns one WebSocket connection's write side: every outbound message
// (ping or event) is serialized through send/run, since coder/websocket
// forbids concurrent writes on one connection.
type client struct {
	conn   *websocket.Conn
	events chan event
}

func newClient(conn *websocket.Conn) *client {
	return &client{conn: conn, events: make(chan event, eventBufferSize)}
}

// send enqueues ev for delivery, dropping it if the client's outbound buffer
// is full rather than blocking the broadcaster on one slow reader.
func (c *client) send(ev event) {
	select {
	case c.events <- ev:
	default:
	}
}

// run drives the write loop (events + periodic ping) until ctx is canceled
// or the connection proves dead (a ping goes unanswered). The caller's read
// loop is what ultimately notices the connection closed and cancels ctx.
func (c *client) run(ctx context.Context) {
	ticker := time.NewTicker(pingInterval)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			return
		case ev := <-c.events:
			writeCtx, cancel := context.WithTimeout(ctx, writeTimeout)
			err := wsjson.Write(writeCtx, c.conn, ev)
			cancel()
			if err != nil {
				_ = c.conn.Close(websocket.StatusInternalError, "write failed")
				return
			}
		case <-ticker.C:
			pingCtx, cancel := context.WithTimeout(ctx, writeTimeout)
			err := c.conn.Ping(pingCtx)
			cancel()
			if err != nil {
				_ = c.conn.Close(websocket.StatusPolicyViolation, "ping timeout")
				return
			}
		}
	}
}
