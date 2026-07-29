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
	CreatedAt    time.Time
}
