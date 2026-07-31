package domain

import (
	"time"

	"github.com/google/uuid"
)

// ProgressPoint is one dated overall_score sample backing the FR-35
// progress chart. The E3 worker denormalizes it into progress_snapshots
// once an analysis's stage 11 aggregation succeeds, so it survives a later
// retry that rewrites the analysis row itself (spec 7).
type ProgressPoint struct {
	AnalysisID   uuid.UUID
	OverallScore float64
	// Mode and Confidence are denormalized off the analysis at write time
	// (spec 7): a client cannot tell two overall_score points computed
	// under different weights_profile apart without them, and FR-49
	// requires the progress chart to visually distinguish clean from mixed
	// points and warn that they are not directly comparable.
	Mode       AnalysisMode
	Confidence *ConfidenceLevel
	CreatedAt  time.Time
}
