package httptransport

import (
	"encoding/json"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/stretchr/testify/require"

	"ai-vocal-coach/api/internal/domain"
)

func float64Ptr(v float64) *float64 { return &v }

func TestNewAnalysisResponse_MapsEveryField(t *testing.T) {
	completedAt := time.Now()
	feedback := "Overall score: 88/100."
	scoringVersion := "1.0"
	pianoRoll := []byte(`{"hop_seconds":0.01,"user_hz":[440.0],"reference_hz":[441.0],` +
		`"deviation_cents":[3.9],"off_pitch":[false]}`)

	a := &domain.Analysis{
		ID:             uuid.New(),
		SongID:         uuid.New(),
		Status:         domain.AnalysisStatusDone,
		PitchScore:     float64Ptr(80),
		RhythmScore:    float64Ptr(90),
		VibratoScore:   float64Ptr(100),
		BreathScore:    float64Ptr(70),
		DynamicsScore:  float64Ptr(60),
		TimbreScore:    float64Ptr(50),
		OverallScore:   float64Ptr(77.5),
		FeedbackText:   &feedback,
		ScoringVersion: &scoringVersion,
		PitchCurveJSON: pianoRoll,
		CreatedAt:      completedAt.Add(-time.Minute),
		CompletedAt:    &completedAt,
	}

	got := newAnalysisResponse(a)

	require.Equal(t, a.ID.String(), got.ID)
	require.Equal(t, a.SongID.String(), got.SongID)
	require.Equal(t, "done", got.Status)
	require.Equal(t, a.PitchScore, got.PitchScore)
	require.Equal(t, a.RhythmScore, got.RhythmScore)
	require.Equal(t, a.VibratoScore, got.VibratoScore)
	require.Equal(t, a.BreathScore, got.BreathScore)
	require.Equal(t, a.DynamicsScore, got.DynamicsScore)
	require.Equal(t, a.TimbreScore, got.TimbreScore)
	require.Equal(t, a.OverallScore, got.OverallScore)
	require.Equal(t, a.FeedbackText, got.FeedbackText)
	require.Equal(t, a.ScoringVersion, got.ScoringVersion)
	require.JSONEq(t, string(pianoRoll), string(got.PianoRoll))

	// The client (web/src/api/client.ts) decodes piano_roll straight off the
	// JSON body, so the wire format matters as much as the Go struct fields.
	body, err := json.Marshal(got)
	require.NoError(t, err)
	require.JSONEq(t, string(pianoRoll), string(json.RawMessage(mustExtract(t, body, "piano_roll"))))
}

func TestNewAnalysisResponse_QueuedAnalysis_OmitsUnsetScoreAndPianoRollFields(t *testing.T) {
	a := &domain.Analysis{
		ID:        uuid.New(),
		SongID:    uuid.New(),
		Status:    domain.AnalysisStatusQueued,
		CreatedAt: time.Now(),
	}

	got := newAnalysisResponse(a)
	body, err := json.Marshal(got)
	require.NoError(t, err)

	var decoded map[string]any
	require.NoError(t, json.Unmarshal(body, &decoded))
	for _, key := range []string{
		"pitch_score", "rhythm_score", "vibrato_score", "breath_score",
		"dynamics_score", "timbre_score", "overall_score",
		"feedback_text", "scoring_version", "piano_roll", "completed_at",
	} {
		_, present := decoded[key]
		require.Falsef(t, present, "queued analysis must not carry a %q field yet", key)
	}
}

func mustExtract(t *testing.T, body []byte, key string) json.RawMessage {
	t.Helper()
	var decoded map[string]json.RawMessage
	require.NoError(t, json.Unmarshal(body, &decoded))
	value, ok := decoded[key]
	require.True(t, ok, "expected %q in response body", key)
	return value
}
