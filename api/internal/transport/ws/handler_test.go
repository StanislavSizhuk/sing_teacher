package ws_test

import (
	"context"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/coder/websocket"
	"github.com/coder/websocket/wsjson"
	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"

	"ai-vocal-coach/api/internal/domain"
	"ai-vocal-coach/api/internal/transport/ws"
)

type fakeTokenParser struct {
	userID uuid.UUID
	err    error
}

func (f fakeTokenParser) Parse(_ string) (uuid.UUID, error) {
	if f.err != nil {
		return uuid.Nil, f.err
	}
	return f.userID, nil
}

type fakeAnalysisReader struct {
	analyses map[uuid.UUID]*domain.Analysis
}

func (f fakeAnalysisReader) GetByID(_ context.Context, id, userID uuid.UUID) (*domain.Analysis, error) {
	a, ok := f.analyses[id]
	if !ok || a.UserID != userID {
		return nil, domain.ErrNotFound
	}
	return a, nil
}

func newTestServer(t *testing.T, hub *ws.Hub, tokens fakeTokenParser, reader fakeAnalysisReader) (*httptest.Server, string) {
	t.Helper()
	handler := ws.NewHandler(hub, reader, tokens, "http://example.com")
	r := chi.NewRouter()
	r.Get("/ws/analyses/{id}", handler.ServeAnalysis)
	server := httptest.NewServer(r)
	t.Cleanup(server.Close)
	wsURL := "ws" + strings.TrimPrefix(server.URL, "http")
	return server, wsURL
}

func TestServeAnalysis_ValidAuth_ReceivesInitialPosition(t *testing.T) {
	hub := ws.NewHub()
	userID := uuid.New()
	analysisID := uuid.New()
	pos := 3
	reader := fakeAnalysisReader{analyses: map[uuid.UUID]*domain.Analysis{
		analysisID: {ID: analysisID, UserID: userID, Status: domain.AnalysisStatusQueued, QueuePosition: &pos},
	}}
	_, wsURL := newTestServer(t, hub, fakeTokenParser{userID: userID}, reader)

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	conn, _, err := websocket.Dial(ctx, wsURL+"/ws/analyses/"+analysisID.String(), nil)
	require.NoError(t, err)
	defer func() { _ = conn.CloseNow() }()

	require.NoError(t, wsjson.Write(ctx, conn, map[string]string{"token": "good-token"}))

	var got map[string]any
	require.NoError(t, wsjson.Read(ctx, conn, &got))
	require.Equal(t, "queued", got["type"])
	require.EqualValues(t, 3, got["position"])
}

func TestServeAnalysis_InvalidToken_ConnectionRejected(t *testing.T) {
	hub := ws.NewHub()
	analysisID := uuid.New()
	reader := fakeAnalysisReader{analyses: map[uuid.UUID]*domain.Analysis{}}
	_, wsURL := newTestServer(t, hub, fakeTokenParser{err: domain.ErrInvalidAccessToken}, reader)

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	conn, _, err := websocket.Dial(ctx, wsURL+"/ws/analyses/"+analysisID.String(), nil)
	require.NoError(t, err)
	defer func() { _ = conn.CloseNow() }()

	require.NoError(t, wsjson.Write(ctx, conn, map[string]string{"token": "bad-token"}))

	var got map[string]any
	require.Error(t, wsjson.Read(ctx, conn, &got), "the server must close the connection on an invalid token")
}

func TestServeAnalysis_WrongOwner_ConnectionRejected(t *testing.T) {
	hub := ws.NewHub()
	owner := uuid.New()
	stranger := uuid.New()
	analysisID := uuid.New()
	reader := fakeAnalysisReader{analyses: map[uuid.UUID]*domain.Analysis{
		analysisID: {ID: analysisID, UserID: owner, Status: domain.AnalysisStatusQueued},
	}}
	_, wsURL := newTestServer(t, hub, fakeTokenParser{userID: stranger}, reader)

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	conn, _, err := websocket.Dial(ctx, wsURL+"/ws/analyses/"+analysisID.String(), nil)
	require.NoError(t, err)
	defer func() { _ = conn.CloseNow() }()

	require.NoError(t, wsjson.Write(ctx, conn, map[string]string{"token": "stranger-token"}))

	var got map[string]any
	require.Error(t, wsjson.Read(ctx, conn, &got), "a different user's analysis must be rejected like it doesn't exist")
}

func TestHub_BroadcastPositions_DeliversToConnectedClient(t *testing.T) {
	hub := ws.NewHub()
	userID := uuid.New()
	analysisID := uuid.New()
	reader := fakeAnalysisReader{analyses: map[uuid.UUID]*domain.Analysis{
		analysisID: {ID: analysisID, UserID: userID, Status: domain.AnalysisStatusQueued},
	}}
	_, wsURL := newTestServer(t, hub, fakeTokenParser{userID: userID}, reader)

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	conn, _, err := websocket.Dial(ctx, wsURL+"/ws/analyses/"+analysisID.String(), nil)
	require.NoError(t, err)
	defer func() { _ = conn.CloseNow() }()
	require.NoError(t, wsjson.Write(ctx, conn, map[string]string{"token": "good-token"}))

	// coder/websocket closes the connection when a Read's context is
	// canceled, so a single Read must run for the whole wait -- reissuing
	// short-timeout reads in a poll loop would kill the socket on the first
	// miss. Registration on the server happens asynchronously right after
	// the auth message, so the broadcast itself is what gets retried, at
	// the hub level, while one long-lived read waits for it to land.
	type result struct {
		msg map[string]any
		err error
	}
	received := make(chan result, 1)
	go func() {
		var got map[string]any
		err := wsjson.Read(ctx, conn, &got)
		received <- result{got, err}
	}()

	ticker := time.NewTicker(50 * time.Millisecond)
	defer ticker.Stop()
	for {
		select {
		case res := <-received:
			require.NoError(t, res.err)
			require.Equal(t, "queued", res.msg["type"])
			require.EqualValues(t, 1, res.msg["position"])
			return
		case <-ticker.C:
			hub.BroadcastPositions(map[uuid.UUID]int{analysisID: 1})
		case <-ctx.Done():
			t.Fatal("timed out waiting for broadcast position update")
		}
	}
}
