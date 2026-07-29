package httptransport

import (
	"time"

	"ai-vocal-coach/api/internal/domain"
)

type progressPointResponse struct {
	AnalysisID   string    `json:"analysis_id"`
	OverallScore float64   `json:"overall_score"`
	CreatedAt    time.Time `json:"created_at"`
}

func newProgressPointResponse(p domain.ProgressPoint) progressPointResponse {
	return progressPointResponse{
		AnalysisID:   p.AnalysisID.String(),
		OverallScore: p.OverallScore,
		CreatedAt:    p.CreatedAt,
	}
}
