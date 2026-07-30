package domain

import (
	"time"

	"github.com/google/uuid"
)

// AnalysisStatus is the lifecycle state of one analysis job (spec 7).
type AnalysisStatus string

// Valid values of AnalysisStatus (migration 00004).
const (
	AnalysisStatusQueued     AnalysisStatus = "queued"
	AnalysisStatusProcessing AnalysisStatus = "processing"
	AnalysisStatusDone       AnalysisStatus = "done"
	AnalysisStatusFailed     AnalysisStatus = "failed"
	AnalysisStatusCanceled   AnalysisStatus = "canceled"
)

// Analysis is one "compare my recording to this song" job: the user's
// recording plus a song, tracked through the queue and (in E3) the ML
// pipeline, down to per-aspect scores.
type Analysis struct {
	ID     uuid.UUID
	UserID uuid.UUID
	SongID uuid.UUID
	Status AnalysisStatus

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
}

// IsQueued reports whether the job is still waiting to be picked up.
func (a *Analysis) IsQueued() bool {
	return a.Status == AnalysisStatusQueued
}
