package postgres

import (
	"context"
	"fmt"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"

	"ai-vocal-coach/api/internal/domain"
)

// progressPointsCap bounds how many points one GET /progress call returns.
// progress_snapshots isn't a browsable list (spec 8.1's cursor pagination is
// for those); it feeds a chart, so a fixed cap is the simpler tool for
// keeping a single request cheap regardless of how long an account has
// existed.
const progressPointsCap = 1000

// ProgressRepository lists a user's progress_snapshots rows.
type ProgressRepository struct {
	pool *pgxpool.Pool
}

// NewProgressRepository builds a ProgressRepository backed by the given pool.
func NewProgressRepository(pool *pgxpool.Pool) *ProgressRepository {
	return &ProgressRepository{pool: pool}
}

// ListByUser returns userID's progress points oldest first, the order a
// chart draws in, capped at progressPointsCap. Mode/Confidence come along
// with every point (spec 7) so the FR-49 chart can tell a clean-mode point
// from a mixed-mode one -- and therefore not directly comparable -- without
// a second round trip to analyses.
func (r *ProgressRepository) ListByUser(ctx context.Context, userID uuid.UUID) ([]domain.ProgressPoint, error) {
	const q = `
		SELECT analysis_id, overall_score, mode, confidence, created_at
		FROM progress_snapshots
		WHERE user_id = $1
		ORDER BY created_at ASC
		LIMIT $2`

	rows, err := r.pool.Query(ctx, q, userID, progressPointsCap)
	if err != nil {
		return nil, fmt.Errorf("list progress snapshots: %w", err)
	}
	defer rows.Close()

	points := make([]domain.ProgressPoint, 0)
	for rows.Next() {
		var p domain.ProgressPoint
		if err := rows.Scan(&p.AnalysisID, &p.OverallScore, &p.Mode, &p.Confidence, &p.CreatedAt); err != nil {
			return nil, fmt.Errorf("scan progress snapshot: %w", err)
		}
		points = append(points, p)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate progress snapshots: %w", err)
	}
	return points, nil
}
