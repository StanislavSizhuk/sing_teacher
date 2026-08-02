package domain

import (
	"time"

	"github.com/google/uuid"
)

// AnalysisMode is which kind of recording an analysis was run against
// (spec 2.3, 6.1): `clean` (a cappella) scores all six aspects at the
// highest accuracy; `mixed` (sung over any accompaniment) scores four,
// via melody extraction instead of direct pitch detection (spec 6.6).
type AnalysisMode string

// Valid values of AnalysisMode (spec 2.3, FR-27, migration 00011).
const (
	AnalysisModeClean AnalysisMode = "clean"
	AnalysisModeMixed AnalysisMode = "mixed"
)

// Locale is the language the FR-32 feedback report is written in
// (ADR-0031): the caller's own choice at POST /analyses, fixed at creation
// the same way Mode is.
type Locale string

// Valid values of Locale (ADR-0031, migration 00013).
const (
	LocaleEN Locale = "en"
	LocaleUK Locale = "uk"
)

// ConfidenceLevel is how reliable an analysis's scores are (spec 6.15,
// FR-47) -- never cosmetic: a mode or measurement that is inherently less
// reliable must say so instead of handing back a precise-looking number.
type ConfidenceLevel string

// Valid values of ConfidenceLevel (spec 6.15, migration 00011).
const (
	ConfidenceHigh   ConfidenceLevel = "high"
	ConfidenceMedium ConfidenceLevel = "medium"
	ConfidenceLow    ConfidenceLevel = "low"
)

// AnalysisStatus is the lifecycle state of one analysis job (spec 7).
type AnalysisStatus string

// Valid values of AnalysisStatus (migration 00004, extended by migration 00010).
const (
	AnalysisStatusQueued     AnalysisStatus = "queued"
	AnalysisStatusProcessing AnalysisStatus = "processing"
	AnalysisStatusDone       AnalysisStatus = "done"
	AnalysisStatusFailed     AnalysisStatus = "failed"
	AnalysisStatusCanceled   AnalysisStatus = "canceled"
	// AnalysisStatusWaitingForReference means the job was created before its
	// song's cold path reached ready; it holds here, not on analyses:run,
	// until the song's prep wakes it into AnalysisStatusQueued (spec 6.2,
	// 10.3, FR-16).
	AnalysisStatusWaitingForReference AnalysisStatus = "waiting_for_reference"
)

// Analysis is one "compare my recording to this song" job: the user's
// recording plus a song, tracked through the queue and (in E3) the ML
// pipeline, down to per-aspect scores.
type Analysis struct {
	ID     uuid.UUID
	UserID uuid.UUID
	SongID uuid.UUID
	Status AnalysisStatus

	// Mode is the user's own choice at POST /analyses (FR-27), clean by
	// default (spec 2.3). EffectiveMode is what the worker's stage A3
	// actually reconciled it to once it saw the recording -- nil until an
	// analysis reaches the recording_condition stage (spec 6.16, FR-29/30).
	Mode               AnalysisMode
	EffectiveMode      *AnalysisMode
	AllowTransposition bool
	// Locale (ADR-0031) is the language the worker writes FeedbackText in,
	// the user's own choice at POST /analyses.
	Locale Locale

	// Confidence/AspectConfidenceJSON/WarningsJSON/UnavailableAspectsJSON
	// are the worker's honesty model (spec 6.14, 6.15, FR-41, FR-47), all
	// nil until stage 11 (aggregate) completes. KeyShiftSemitones,
	// AccompanimentLevel, VoicedRatio and AlignmentCost are the diagnostic
	// signals behind Confidence (spec 6.15's table); WeightsProfile records
	// which named profile OverallScore was computed under (spec 6.14) so a
	// stored score stays interpretable after the formula changes.
	Confidence             *ConfidenceLevel
	AspectConfidenceJSON   []byte
	WarningsJSON           []byte
	UnavailableAspectsJSON []byte
	KeyShiftSemitones      *float64
	AccompanimentLevel     *float64
	VoicedRatio            *float64
	AlignmentCost          *float64
	WeightsProfile         *string

	// QueuePosition is this job's 1-based place among currently queued jobs,
	// recomputed by AnalysisRepository.RecalculatePositions on every queue
	// change (enqueue, cancel) and pushed to WS clients (spec 10, FR-23). Nil
	// once the job leaves the queued state.
	QueuePosition *int
	// QueueSeq orders queued jobs FIFO; assigned once at creation, never reused.
	QueueSeq int64
	// QueueStreamID is the Redis Streams entry id returned by XADD (ADR-0002),
	// kept so Cancel can XDEL the exact entry. Nil until enqueued in Redis.
	QueueStreamID *string

	CurrentStage *string
	// CurrentStageIndex/TotalStages are the same 1-based position/count the
	// WS `stage` event carries (spec 8.3), persisted so REST agrees with it
	// and a fresh page load (or the polling fallback) can render "stage N
	// of M" without waiting on a WS message. CurrentStageStartedAt is when
	// CurrentStage began, so the client can render a live elapsed timer
	// instead of a static label that looks frozen during a multi-minute
	// stage (spec 6.2's Demucs/Whisper timeouts).
	CurrentStageIndex     *int
	TotalStages           *int
	CurrentStageStartedAt *time.Time
	ErrorCode             *string

	PitchScore    *float64
	RhythmScore   *float64
	VibratoScore  *float64
	BreathScore   *float64
	DynamicsScore *float64
	TimbreScore   *float64
	OverallScore  *float64

	// PitchCurveJSON, StagesJSON, ModelVersions are ML pipeline output (spec
	// 6.7, 6.1), populated once the worker exists (stage E3).
	PitchCurveJSON []byte
	StagesJSON     []byte
	FeedbackText   *string
	ScoringVersion *string
	ModelVersions  []byte

	CreatedAt   time.Time
	CompletedAt *time.Time
	// QueuedAt is when the *current* queued/waiting_for_reference wait
	// began (spec 10, FR-22's live timer) -- equal to CreatedAt for a
	// fresh submission, reset to the retry time by Retry/
	// RetryToWaitingForReference (FR-26), since that reuses the same row
	// rather than creating a new one.
	QueuedAt time.Time
}

// IsQueued reports whether the job is still waiting to be picked up.
func (a *Analysis) IsQueued() bool {
	return a.Status == AnalysisStatusQueued
}
