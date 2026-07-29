package httptransport

import (
	"encoding/json"
	"time"

	"ai-vocal-coach/api/internal/domain"
)

type analysisResponse struct {
	ID             string   `json:"id"`
	SongID         string   `json:"song_id"`
	Status         string   `json:"status"`
	QueuePosition  *int     `json:"queue_position,omitempty"`
	CurrentStage   *string  `json:"current_stage,omitempty"`
	ErrorCode      *string  `json:"error_code,omitempty"`
	PitchScore     *float64 `json:"pitch_score,omitempty"`
	RhythmScore    *float64 `json:"rhythm_score,omitempty"`
	VibratoScore   *float64 `json:"vibrato_score,omitempty"`
	BreathScore    *float64 `json:"breath_score,omitempty"`
	DynamicsScore  *float64 `json:"dynamics_score,omitempty"`
	TimbreScore    *float64 `json:"timbre_score,omitempty"`
	OverallScore   *float64 `json:"overall_score,omitempty"`
	FeedbackText   *string  `json:"feedback_text,omitempty"`
	ScoringVersion *string  `json:"scoring_version,omitempty"`
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
		ID:             a.ID.String(),
		SongID:         a.SongID.String(),
		Status:         string(a.Status),
		QueuePosition:  a.QueuePosition,
		CurrentStage:   a.CurrentStage,
		ErrorCode:      a.ErrorCode,
		PitchScore:     a.PitchScore,
		RhythmScore:    a.RhythmScore,
		VibratoScore:   a.VibratoScore,
		BreathScore:    a.BreathScore,
		DynamicsScore:  a.DynamicsScore,
		TimbreScore:    a.TimbreScore,
		OverallScore:   a.OverallScore,
		FeedbackText:   a.FeedbackText,
		ScoringVersion: a.ScoringVersion,
		PianoRoll:      json.RawMessage(a.PitchCurveJSON),
		CreatedAt:      a.CreatedAt,
		CompletedAt:    a.CompletedAt,
	}
}
