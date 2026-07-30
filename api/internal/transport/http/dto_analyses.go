package httptransport

import (
	"encoding/json"
	"time"

	"ai-vocal-coach/api/internal/domain"
)

// stageProgress is the small, display-only subset of a worker StageResult
// (spec 6.1) exposed per completed stage: enough for the client to render
// a per-stage duration breakdown, never the stage's own `data` (which can
// carry a full pitch curve for the "pitch" stage, already exposed
// separately as pitch_curve_json) or its error detail.
type stageProgress struct {
	Status     string `json:"status"`
	DurationMs int    `json:"duration_ms"`
}

type analysisResponse struct {
	ID            string  `json:"id"`
	SongID        string  `json:"song_id"`
	Status        string  `json:"status"`
	QueuePosition *int    `json:"queue_position,omitempty"`
	CurrentStage  *string `json:"current_stage,omitempty"`
	// CurrentStageIndex/TotalStages/CurrentStageStartedAt let the client
	// render "stage N of M" plus a live elapsed timer (spec 6.2) instead of
	// a bare stage name that looks frozen during a multi-minute stage.
	CurrentStageIndex     *int       `json:"current_stage_index,omitempty"`
	TotalStages           *int       `json:"total_stages,omitempty"`
	CurrentStageStartedAt *time.Time `json:"current_stage_started_at,omitempty"`
	// Stages is every already-completed stage's real recorded duration,
	// keyed by stage name, so the client can show a running timeline
	// instead of only the current stage.
	Stages         map[string]stageProgress `json:"stages,omitempty"`
	ErrorCode      *string                  `json:"error_code,omitempty"`
	PitchScore     *float64                 `json:"pitch_score,omitempty"`
	RhythmScore    *float64                 `json:"rhythm_score,omitempty"`
	VibratoScore   *float64                 `json:"vibrato_score,omitempty"`
	BreathScore    *float64                 `json:"breath_score,omitempty"`
	DynamicsScore  *float64                 `json:"dynamics_score,omitempty"`
	TimbreScore    *float64                 `json:"timbre_score,omitempty"`
	OverallScore   *float64                 `json:"overall_score,omitempty"`
	FeedbackText   *string                  `json:"feedback_text,omitempty"`
	ScoringVersion *string                  `json:"scoring_version,omitempty"`
	// PianoRoll is analyses.pitch_curve_json passed through untouched: the
	// worker (worker/src/vocalcoach/models/audio.py PianoRollData) already
	// writes it in exactly the shape components.schemas.PianoRoll declares,
	// so re-decoding it into a matching Go struct just to re-encode the same
	// bytes would be pure overhead with a chance to drift from the JSON the
	// worker actually wrote.
	PianoRoll   json.RawMessage `json:"piano_roll,omitempty"`
	CreatedAt   time.Time       `json:"created_at"`
	CompletedAt *time.Time      `json:"completed_at,omitempty"`
}

func newAnalysisResponse(a *domain.Analysis) analysisResponse {
	return analysisResponse{
		ID:                    a.ID.String(),
		SongID:                a.SongID.String(),
		Status:                string(a.Status),
		QueuePosition:         a.QueuePosition,
		CurrentStage:          a.CurrentStage,
		CurrentStageIndex:     a.CurrentStageIndex,
		TotalStages:           a.TotalStages,
		CurrentStageStartedAt: a.CurrentStageStartedAt,
		Stages:                stageSummaries(a.StagesJSON),
		ErrorCode:             a.ErrorCode,
		PitchScore:            a.PitchScore,
		RhythmScore:           a.RhythmScore,
		VibratoScore:          a.VibratoScore,
		BreathScore:           a.BreathScore,
		DynamicsScore:         a.DynamicsScore,
		TimbreScore:           a.TimbreScore,
		OverallScore:          a.OverallScore,
		FeedbackText:          a.FeedbackText,
		ScoringVersion:        a.ScoringVersion,
		PianoRoll:             json.RawMessage(a.PitchCurveJSON),
		CreatedAt:             a.CreatedAt,
		CompletedAt:           a.CompletedAt,
	}
}

// stageSummaries extracts each completed stage's status/duration out of
// stages_json, dropping everything else (spec 6.1's data/error fields).
// raw is written exclusively by our own worker under a schema it controls
// (models/results.py StageResult); a decode failure here would mean that
// contract broke, not a bad user input, and this field is purely a display
// convenience, so it degrades to omitted rather than failing the whole
// analysis response over it.
func stageSummaries(raw []byte) map[string]stageProgress {
	if len(raw) == 0 {
		return nil
	}
	var summaries map[string]stageProgress
	if err := json.Unmarshal(raw, &summaries); err != nil {
		return nil
	}
	return summaries
}
