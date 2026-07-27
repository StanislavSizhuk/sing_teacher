package httptransport

import (
	"time"

	"ai-vocal-coach/api/internal/domain"
)

type analysisResponse struct {
	ID            string     `json:"id"`
	SongID        string     `json:"song_id"`
	Status        string     `json:"status"`
	QueuePosition *int       `json:"queue_position,omitempty"`
	CurrentStage  *string    `json:"current_stage,omitempty"`
	ErrorCode     *string    `json:"error_code,omitempty"`
	OverallScore  *float64   `json:"overall_score,omitempty"`
	CreatedAt     time.Time  `json:"created_at"`
	CompletedAt   *time.Time `json:"completed_at,omitempty"`
}

func newAnalysisResponse(a *domain.Analysis) analysisResponse {
	return analysisResponse{
		ID:            a.ID.String(),
		SongID:        a.SongID.String(),
		Status:        string(a.Status),
		QueuePosition: a.QueuePosition,
		CurrentStage:  a.CurrentStage,
		ErrorCode:     a.ErrorCode,
		OverallScore:  a.OverallScore,
		CreatedAt:     a.CreatedAt,
		CompletedAt:   a.CompletedAt,
	}
}
