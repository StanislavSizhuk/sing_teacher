package ws_test

import (
	"context"
	"testing"
	"time"

	"github.com/coder/websocket"
	"github.com/coder/websocket/wsjson"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"

	"ai-vocal-coach/api/internal/domain"
	"ai-vocal-coach/api/internal/transport/ws"
)

// connectAndAuth dials the WS endpoint, authenticates, and returns the
// connection ready to receive broadcasts.
func connectAndAuth(ctx context.Context, t *testing.T, wsURL string, analysisID uuid.UUID) *websocket.Conn {
	t.Helper()
	conn, _, err := websocket.Dial(ctx, wsURL+"/ws/analyses/"+analysisID.String(), nil)
	require.NoError(t, err)
	t.Cleanup(func() { _ = conn.CloseNow() })
	require.NoError(t, wsjson.Write(ctx, conn, map[string]string{"token": "good-token"}))
	return conn
}

// waitForBroadcast retries broadcast (hub registration happens
// asynchronously right after auth, spec: see the position-broadcast test)
// until a message arrives on conn or ctx expires.
func waitForBroadcast(ctx context.Context, t *testing.T, conn *websocket.Conn, broadcast func()) map[string]any {
	t.Helper()
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
			return res.msg
		case <-ticker.C:
			broadcast()
		case <-ctx.Done():
			t.Fatal("timed out waiting for broadcast")
			return nil
		}
	}
}

func TestHub_BroadcastStage_DeliversToConnectedClient(t *testing.T) {
	hub := ws.NewHub()
	userID := uuid.New()
	analysisID := uuid.New()
	reader := fakeAnalysisReader{analyses: map[uuid.UUID]*domain.Analysis{
		analysisID: {ID: analysisID, UserID: userID, Status: domain.AnalysisStatusProcessing},
	}}
	_, wsURL := newTestServer(t, hub, fakeTokenParser{userID: userID}, reader)

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	conn := connectAndAuth(ctx, t, wsURL, analysisID)

	msg := waitForBroadcast(ctx, t, conn, func() {
		hub.BroadcastStage(analysisID, "pitch", 5, 10)
	})

	require.Equal(t, "stage", msg["type"])
	require.Equal(t, "pitch", msg["name"])
	require.EqualValues(t, 5, msg["index"])
	require.EqualValues(t, 10, msg["total"])
}

func TestHub_BroadcastDone_DeliversToConnectedClient(t *testing.T) {
	hub := ws.NewHub()
	userID := uuid.New()
	analysisID := uuid.New()
	reader := fakeAnalysisReader{analyses: map[uuid.UUID]*domain.Analysis{
		analysisID: {ID: analysisID, UserID: userID, Status: domain.AnalysisStatusProcessing},
	}}
	_, wsURL := newTestServer(t, hub, fakeTokenParser{userID: userID}, reader)

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	conn := connectAndAuth(ctx, t, wsURL, analysisID)

	msg := waitForBroadcast(ctx, t, conn, func() {
		hub.BroadcastDone(analysisID)
	})

	require.Equal(t, "done", msg["type"])
	require.Equal(t, analysisID.String(), msg["analysis_id"])
}

func TestHub_BroadcastFailed_DeliversToConnectedClient(t *testing.T) {
	hub := ws.NewHub()
	userID := uuid.New()
	analysisID := uuid.New()
	reader := fakeAnalysisReader{analyses: map[uuid.UUID]*domain.Analysis{
		analysisID: {ID: analysisID, UserID: userID, Status: domain.AnalysisStatusProcessing},
	}}
	_, wsURL := newTestServer(t, hub, fakeTokenParser{userID: userID}, reader)

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	conn := connectAndAuth(ctx, t, wsURL, analysisID)

	msg := waitForBroadcast(ctx, t, conn, func() {
		hub.BroadcastFailed(analysisID, "NO_VOICE_DETECTED", "no singing detected")
	})

	require.Equal(t, "failed", msg["type"])
	require.Equal(t, "NO_VOICE_DETECTED", msg["error_code"])
	require.Equal(t, "no singing detected", msg["message"])
}

func TestHub_BroadcastStage_OnlyReachesConnectedAnalysis(t *testing.T) {
	hub := ws.NewHub()
	userID := uuid.New()
	watchedID := uuid.New()
	otherID := uuid.New()
	reader := fakeAnalysisReader{analyses: map[uuid.UUID]*domain.Analysis{
		watchedID: {ID: watchedID, UserID: userID, Status: domain.AnalysisStatusProcessing},
	}}
	_, wsURL := newTestServer(t, hub, fakeTokenParser{userID: userID}, reader)

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	conn := connectAndAuth(ctx, t, wsURL, watchedID)

	msg := waitForBroadcast(ctx, t, conn, func() {
		hub.BroadcastStage(otherID, "pitch", 1, 10) // a different analysis entirely
		hub.BroadcastStage(watchedID, "align", 4, 10)
	})

	require.Equal(t, "align", msg["name"], "must not receive the event for a different analysis")
}
