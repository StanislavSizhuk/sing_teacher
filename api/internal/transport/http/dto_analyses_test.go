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

	effectiveMode := domain.AnalysisModeMixed
	confidence := domain.ConfidenceMedium
	weightsProfile := "mixed_v1"
	aspectConfidenceJSON := []byte(`{"pitch":"medium"}`)
	warningsJSON := []byte(`["KEY_SHIFT_APPLIED"]`)
	unavailableAspectsJSON := []byte(`{"breath":"NOT_MEASURABLE_WITH_ACCOMPANIMENT"}`)

	a := &domain.Analysis{
		ID:                     uuid.New(),
		SongID:                 uuid.New(),
		Status:                 domain.AnalysisStatusDone,
		Mode:                   domain.AnalysisModeMixed,
		EffectiveMode:          &effectiveMode,
		CurrentStage:           &currentStage,
		CurrentStageIndex:      intPtr(5),
		TotalStages:            intPtr(11),
		CurrentStageStartedAt:  &stageStartedAt,
		StagesJSON:             stagesJSON,
		PitchScore:             float64Ptr(80),
		RhythmScore:            float64Ptr(90),
		VibratoScore:           float64Ptr(100),
		BreathScore:            float64Ptr(70),
		DynamicsScore:          float64Ptr(60),
		TimbreScore:            float64Ptr(50),
		OverallScore:           float64Ptr(77.5),
		FeedbackText:           &feedback,
		ScoringVersion:         &scoringVersion,
		WeightsProfile:         &weightsProfile,
		Confidence:             &confidence,
		AspectConfidenceJSON:   aspectConfidenceJSON,
		WarningsJSON:           warningsJSON,
		UnavailableAspectsJSON: unavailableAspectsJSON,
		KeyShiftSemitones:      float64Ptr(-2),
		PitchCurveJSON:         pianoRoll,
		CreatedAt:              completedAt.Add(-time.Minute),
		CompletedAt:            &completedAt,
	}

	got := newAnalysisResponse(a)

	require.Equal(t, a.ID.String(), got.ID)
	require.Equal(t, a.SongID.String(), got.SongID)
	require.Equal(t, "done", got.Status)
	require.Equal(t, "mixed", got.Mode)
	require.NotNil(t, got.EffectiveMode)
	require.Equal(t, "mixed", *got.EffectiveMode)
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
	require.Equal(t, &weightsProfile, got.WeightsProfile)
	require.NotNil(t, got.Confidence)
	require.Equal(t, "medium", *got.Confidence)
	require.Equal(t, map[string]string{"pitch": "medium"}, got.AspectConfidence)
	require.Equal(t, []string{"KEY_SHIFT_APPLIED"}, got.Warnings)
	require.Equal(t, map[string]string{"breath": "NOT_MEASURABLE_WITH_ACCOMPANIMENT"}, got.UnavailableAspects)
	require.Equal(t, float64Ptr(-2), got.KeyShiftSemitones)
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
		Mode:      domain.AnalysisModeClean,
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
		"effective_mode", "weights_profile", "confidence", "aspect_confidence",
		"warnings", "unavailable_aspects", "key_shift_semitones",
	} {
		_, present := decoded[key]
		require.Falsef(t, present, "queued analysis must not carry a %q field yet", key)
	}
	// mode is FR-27's own required field -- it must always be present, even
	// for a just-queued analysis, unlike everything the worker fills in later.
	require.Equal(t, "clean", decoded["mode"])
}

func TestValidateAnalysisMode(t *testing.T) {
	t.Run("empty defaults to clean (spec 2.3)", func(t *testing.T) {
		got, err := validateAnalysisMode("")
		require.NoError(t, err)
		require.Equal(t, domain.AnalysisModeClean, got)
	})

	t.Run("accepts clean and mixed", func(t *testing.T) {
		got, err := validateAnalysisMode("mixed")
		require.NoError(t, err)
		require.Equal(t, domain.AnalysisModeMixed, got)
	})

	t.Run("rejects anything else", func(t *testing.T) {
		_, err := validateAnalysisMode("acapella")
		require.Error(t, err)
	})
}

func TestParseAllowTransposition(t *testing.T) {
	t.Run("absent field defaults per mode (FR-31)", func(t *testing.T) {
		require.False(t, parseAllowTransposition("", domain.AnalysisModeClean))
		require.True(t, parseAllowTransposition("", domain.AnalysisModeMixed))
	})

	t.Run("explicit value overrides the default either way", func(t *testing.T) {
		require.True(t, parseAllowTransposition("true", domain.AnalysisModeClean))
		require.False(t, parseAllowTransposition("false", domain.AnalysisModeMixed))
	})

	t.Run("unparseable value falls back to the mode default", func(t *testing.T) {
		require.False(t, parseAllowTransposition("nonsense", domain.AnalysisModeClean))
	})
}

func TestDecodeStringMap(t *testing.T) {
	require.Nil(t, decodeStringMap(nil))
	require.Nil(t, decodeStringMap([]byte(`not json`)))
	require.Equal(t, map[string]string{"pitch": "high"}, decodeStringMap([]byte(`{"pitch":"high"}`)))
}

func TestDecodeStringSlice(t *testing.T) {
	require.Nil(t, decodeStringSlice(nil))
	require.Nil(t, decodeStringSlice([]byte(`not json`)))
	require.Equal(t, []string{"A", "B"}, decodeStringSlice([]byte(`["A","B"]`)))
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
