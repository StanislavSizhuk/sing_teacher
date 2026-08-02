package httptransport

import (
	"encoding/json"
	"errors"
	"time"

	"ai-vocal-coach/api/internal/domain"
)

// validateAnalysisMode parses the mode form field (FR-27, spec 8.3):
// clean is the recommended default (spec 2.3) when the field is absent, so
// every existing client that predates mode selection keeps working exactly
// as before.
func validateAnalysisMode(raw string) (domain.AnalysisMode, error) {
	switch domain.AnalysisMode(raw) {
	case "":
		return domain.AnalysisModeClean, nil
	case domain.AnalysisModeClean, domain.AnalysisModeMixed:
		return domain.AnalysisMode(raw), nil
	default:
		return "", errors.New("mode must be 'clean' or 'mixed'")
	}
}

// validateLocale parses the locale form field (ADR-0031): "en" is the
// default when the field is absent, so every existing client that predates
// locale selection keeps working exactly as before.
func validateLocale(raw string) (domain.Locale, error) {
	switch domain.Locale(raw) {
	case "":
		return domain.LocaleEN, nil
	case domain.LocaleEN, domain.LocaleUK:
		return domain.Locale(raw), nil
	default:
		return "", errors.New("locale must be 'en' or 'uk'")
	}
}

// defaultAllowTransposition is spec 8.3/FR-31's mode-dependent default: off
// in clean (nothing to transpose to when singing straight over the
// reference in headphones), on in mixed.
func defaultAllowTransposition(mode domain.AnalysisMode) bool {
	return mode == domain.AnalysisModeMixed
}

// parseAllowTransposition reads the optional allow_transposition override
// (FR-31, spec 6.8); an absent or unparseable field falls back to the
// mode's own default rather than rejecting the request over a boundary
// field the spec never requires the client to send.
func parseAllowTransposition(raw string, mode domain.AnalysisMode) bool {
	switch raw {
	case "":
		return defaultAllowTransposition(mode)
	case "true":
		return true
	case "false":
		return false
	default:
		return defaultAllowTransposition(mode)
	}
}

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
	ID     string `json:"id"`
	SongID string `json:"song_id"`
	Status string `json:"status"`
	// Mode is the user's own FR-27 choice; EffectiveMode is what stage A3
	// actually reconciled it to once it saw the recording (spec 6.16),
	// absent until that stage completes.
	Mode          string  `json:"mode"`
	EffectiveMode *string `json:"effective_mode,omitempty"`
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
	// WeightsProfile is which named profile (clean_v1/mixed_v1, spec 6.14)
	// OverallScore was computed under -- a client must never compare
	// OverallScore across two analyses with different WeightsProfile
	// without flagging that to the user (FR-49).
	WeightsProfile *string `json:"weights_profile,omitempty"`
	// Confidence/AspectConfidence/Warnings/UnavailableAspects are the
	// worker's honesty model (spec 6.15, FR-41, FR-47), absent until stage
	// 11 (aggregate) completes. An aspect this mode never scores is never
	// 0 -- it is missing from the score fields above and present here with
	// a machine-readable reason (FR-41).
	Confidence         *string           `json:"confidence,omitempty"`
	AspectConfidence   map[string]string `json:"aspect_confidence,omitempty"`
	Warnings           []string          `json:"warnings,omitempty"`
	UnavailableAspects map[string]string `json:"unavailable_aspects,omitempty"`
	KeyShiftSemitones  *float64          `json:"key_shift_semitones,omitempty"`
	// PianoRoll is analyses.pitch_curve_json passed through untouched: the
	// worker (worker/src/vocalcoach/models/audio.py PianoRollData) already
	// writes it in exactly the shape components.schemas.PianoRoll declares,
	// so re-decoding it into a matching Go struct just to re-encode the same
	// bytes would be pure overhead with a chance to drift from the JSON the
	// worker actually wrote.
	PianoRoll   json.RawMessage `json:"piano_roll,omitempty"`
	CreatedAt   time.Time       `json:"created_at"`
	CompletedAt *time.Time      `json:"completed_at,omitempty"`
	// QueuedAt is when the *current* queued/waiting_for_reference wait
	// began -- equal to CreatedAt for a fresh submission, reset by Retry
	// (FR-26) so the client's live wait timer never measures from a stale
	// original submission (spec 10, FR-22).
	QueuedAt time.Time `json:"queued_at"`
}

func newAnalysisResponse(a *domain.Analysis) analysisResponse {
	var effectiveMode *string
	if a.EffectiveMode != nil {
		s := string(*a.EffectiveMode)
		effectiveMode = &s
	}
	var confidence *string
	if a.Confidence != nil {
		s := string(*a.Confidence)
		confidence = &s
	}
	return analysisResponse{
		ID:                    a.ID.String(),
		SongID:                a.SongID.String(),
		Status:                string(a.Status),
		Mode:                  string(a.Mode),
		EffectiveMode:         effectiveMode,
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
		WeightsProfile:        a.WeightsProfile,
		Confidence:            confidence,
		AspectConfidence:      decodeStringMap(a.AspectConfidenceJSON),
		Warnings:              decodeStringSlice(a.WarningsJSON),
		UnavailableAspects:    decodeStringMap(a.UnavailableAspectsJSON),
		KeyShiftSemitones:     a.KeyShiftSemitones,
		PianoRoll:             json.RawMessage(a.PitchCurveJSON),
		CreatedAt:             a.CreatedAt,
		CompletedAt:           a.CompletedAt,
		QueuedAt:              a.QueuedAt,
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

// decodeStringMap decodes aspect_confidence_json/unavailable_aspects_json,
// both worker-written aspect-name -> string maps (spec 6.14, 6.15). Same
// "degrade to omitted, never fail the response" rule as stageSummaries.
func decodeStringMap(raw []byte) map[string]string {
	if len(raw) == 0 {
		return nil
	}
	var decoded map[string]string
	if err := json.Unmarshal(raw, &decoded); err != nil {
		return nil
	}
	return decoded
}

// decodeStringSlice decodes warnings_json, a worker-written list of
// machine-readable warning codes (spec 6.18). Same degrade-on-failure rule
// as stageSummaries/decodeStringMap.
func decodeStringSlice(raw []byte) []string {
	if len(raw) == 0 {
		return nil
	}
	var decoded []string
	if err := json.Unmarshal(raw, &decoded); err != nil {
		return nil
	}
	return decoded
}
