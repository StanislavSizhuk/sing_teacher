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
func intPtr(v int) *int             { return &v }

func TestNewAnalysisResponse_MapsEveryField(t *testing.T) {
	completedAt := time.Now()
	stageStartedAt := completedAt.Add(-30 * time.Second)
	feedback := "Overall score: 88/100."
	scoringVersion := "1.0"
	currentStage := "pitch"
	pianoRoll := []byte(`{"hop_seconds":0.01,"user_hz":[440.0],"reference_hz":[441.0],` +
		`"deviation_cents":[3.9],"off_pitch":[false]}`)
	stagesJSON := []byte(
		`{"preprocess":{"stage":"preprocess","status":"done","duration_ms":1200,"data":{}}}`,
	)

	a := &domain.Analysis{
		ID:                    uuid.New(),
		SongID:                uuid.New(),
		Status:                domain.AnalysisStatusDone,
		CurrentStage:          &currentStage,
		CurrentStageIndex:     intPtr(5),
		TotalStages:           intPtr(11),
		CurrentStageStartedAt: &stageStartedAt,
		StagesJSON:            stagesJSON,
		PitchScore:            float64Ptr(80),
		RhythmScore:           float64Ptr(90),
		VibratoScore:          float64Ptr(100),
		BreathScore:           float64Ptr(70),
		DynamicsScore:         float64Ptr(60),
		TimbreScore:           float64Ptr(50),
		OverallScore:          float64Ptr(77.5),
		FeedbackText:          &feedback,
		ScoringVersion:        &scoringVersion,
		PitchCurveJSON:        pianoRoll,
		CreatedAt:             completedAt.Add(-time.Minute),
		CompletedAt:           &completedAt,
	}

	got := newAnalysisResponse(a)

	require.Equal(t, a.ID.String(), got.ID)
	require.Equal(t, a.SongID.String(), got.SongID)
	require.Equal(t, "done", got.Status)
	require.Equal(t, a.CurrentStage, got.CurrentStage)
	require.Equal(t, a.CurrentStageIndex, got.CurrentStageIndex)
	require.Equal(t, a.TotalStages, got.TotalStages)
	require.Equal(t, a.CurrentStageStartedAt, got.CurrentStageStartedAt)
	require.Equal(t, map[string]stageProgress{"preprocess": {Status: "done", DurationMs: 1200}}, got.Stages)
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
		"current_stage_index", "total_stages", "current_stage_started_at", "stages",
	} {
		_, present := decoded[key]
		require.Falsef(t, present, "queued analysis must not carry a %q field yet", key)
	}
}

func TestStageSummaries(t *testing.T) {
	t.Run("empty input", func(t *testing.T) {
		require.Nil(t, stageSummaries(nil))
	})

	t.Run("drops stage data and error fields, keeps status and duration", func(t *testing.T) {
		raw := []byte(`{"pitch":{"stage":"pitch","status":"done","duration_ms":4200,
			"data":{"piano_roll":{"huge":"blob"}},"error_code":null,"error_message":null}}`)

		got := stageSummaries(raw)

		require.Equal(t, map[string]stageProgress{"pitch": {Status: "done", DurationMs: 4200}}, got)
	})

	t.Run("malformed json degrades to omitted rather than failing the response", func(t *testing.T) {
		require.Nil(t, stageSummaries([]byte(`not json`)))
	})
}

func mustExtract(t *testing.T, body []byte, key string) json.RawMessage {
	t.Helper()
	var decoded map[string]json.RawMessage
	require.NoError(t, json.Unmarshal(body, &decoded))
	value, ok := decoded[key]
	require.True(t, ok, "expected %q in response body", key)
	return value
}
