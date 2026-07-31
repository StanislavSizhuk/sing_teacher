package httptransport

import (
	"time"

	"ai-vocal-coach/api/internal/domain"
)

type progressPointResponse struct {
	AnalysisID   string  `json:"analysis_id"`
	OverallScore float64 `json:"overall_score"`
	// Mode/Confidence let the client tell points computed under different
	// weights_profile apart (spec 6.14) and visually distinguish them on
	// the chart, per FR-49's requirement that clean and mixed scores not
	// be presented as directly comparable.
	Mode       string    `json:"mode"`
	Confidence *string   `json:"confidence,omitempty"`
	CreatedAt  time.Time `json:"created_at"`
}

func newProgressPointResponse(p domain.ProgressPoint) progressPointResponse {
	var confidence *string
	if p.Confidence != nil {
		s := string(*p.Confidence)
		confidence = &s
	}
	return progressPointResponse{
		AnalysisID:   p.AnalysisID.String(),
		OverallScore: p.OverallScore,
		Mode:         string(p.Mode),
		Confidence:   confidence,
		CreatedAt:    p.CreatedAt,
	}
}
