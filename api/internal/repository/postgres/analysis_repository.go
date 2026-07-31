package postgres

import (
	"context"
	"errors"
	"fmt"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"

	"ai-vocal-coach/api/internal/domain"
)

// AnalysisRepository persists domain.Analysis rows in Postgres.
type AnalysisRepository struct {
	pool *pgxpool.Pool
}

// NewAnalysisRepository builds an AnalysisRepository backed by the given pool.
func NewAnalysisRepository(pool *pgxpool.Pool) *AnalysisRepository {
	return &AnalysisRepository{pool: pool}
}

const analysisColumns = `id, user_id, song_id, status, queue_position, current_stage,
	current_stage_index, total_stages, current_stage_started_at, error_code,
	pitch_score, rhythm_score, vibrato_score, breath_score, dynamics_score, timbre_score, overall_score,
	pitch_curve_json, stages_json, feedback_text, scoring_version, model_versions,
	created_at, completed_at, queue_seq, queue_stream_id`

func scanAnalysis(row pgx.Row) (*domain.Analysis, error) {
	var a domain.Analysis
	err := row.Scan(
		&a.ID, &a.UserID, &a.SongID, &a.Status, &a.QueuePosition, &a.CurrentStage,
		&a.CurrentStageIndex, &a.TotalStages, &a.CurrentStageStartedAt, &a.ErrorCode,
		&a.PitchScore, &a.RhythmScore, &a.VibratoScore, &a.BreathScore, &a.DynamicsScore, &a.TimbreScore, &a.OverallScore,
		&a.PitchCurveJSON, &a.StagesJSON, &a.FeedbackText, &a.ScoringVersion, &a.ModelVersions,
		&a.CreatedAt, &a.CompletedAt, &a.QueueSeq, &a.QueueStreamID,
	)
	if err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, domain.ErrNotFound
		}
		return nil, fmt.Errorf("scan analysis: %w", err)
	}
	return &a, nil
}

// Create inserts a new analysis job. a.Status must be AnalysisStatusQueued;
// a.CreatedAt and a.QueueSeq are filled in from the row on return.
func (r *AnalysisRepository) Create(ctx context.Context, a *domain.Analysis) error {
	const q = `
		INSERT INTO analyses (id, user_id, song_id, status, created_at)
		VALUES ($1, $2, $3, $4, now())
		RETURNING created_at, queue_seq`
	if err := r.pool.QueryRow(ctx, q, a.ID, a.UserID, a.SongID, a.Status).Scan(&a.CreatedAt, &a.QueueSeq); err != nil {
		return fmt.Errorf("create analysis: %w", err)
	}
	return nil
}

// Delete removes a row outright. The only caller is
// analysis.Service.Enqueue, undoing its own just-created row when the
// queue turns out to have filled up between the pre-check and the atomic
// admission (see queue.Producer.EnqueueIfUnderLimit) -- never a
// user-reachable operation, so it takes no ownership scope.
func (r *AnalysisRepository) Delete(ctx context.Context, id uuid.UUID) error {
	const q = `DELETE FROM analyses WHERE id = $1`
	if _, err := r.pool.Exec(ctx, q, id); err != nil {
		return fmt.Errorf("delete analysis: %w", err)
	}
	return nil
}

// SetQueueStreamID records the Redis Streams entry id returned by XADD, so a
// later Cancel can XDEL that exact entry (ADR-0002, ADR-0008).
func (r *AnalysisRepository) SetQueueStreamID(ctx context.Context, id uuid.UUID, streamEntryID string) error {
	const q = `UPDATE analyses SET queue_stream_id = $2 WHERE id = $1`
	ct, err := r.pool.Exec(ctx, q, id, streamEntryID)
	if err != nil {
		return fmt.Errorf("set queue stream id: %w", err)
	}
	if ct.RowsAffected() == 0 {
		return domain.ErrNotFound
	}
	return nil
}

// GetByID returns the analysis with this id, scoped to its owner: a mismatch
// on either id or userID reports domain.ErrNotFound, never distinguishing
// "doesn't exist" from "belongs to someone else" (spec 11: authorization
// checked on every resource).
func (r *AnalysisRepository) GetByID(ctx context.Context, id, userID uuid.UUID) (*domain.Analysis, error) {
	q := `SELECT ` + analysisColumns + ` FROM analyses WHERE id = $1 AND user_id = $2`
	return scanAnalysis(r.pool.QueryRow(ctx, q, id, userID))
}

// Cancel moves a queued or waiting_for_reference analysis to canceled
// (FR-25, spec 6.2/10.3). It returns domain.ErrNotFound if the id doesn't
// exist or isn't owned by userID, and domain.ErrAnalysisNotQueued if it
// exists but has already left one of those two cancelable states.
func (r *AnalysisRepository) Cancel(ctx context.Context, id, userID uuid.UUID) (*domain.Analysis, error) {
	const q = `
		UPDATE analyses SET status = 'canceled', queue_position = NULL
		WHERE id = $1 AND user_id = $2 AND status IN ('queued', 'waiting_for_reference')
		RETURNING ` + analysisColumns

	updated, err := scanAnalysis(r.pool.QueryRow(ctx, q, id, userID))
	if err == nil {
		return updated, nil
	}
	if !errors.Is(err, domain.ErrNotFound) {
		return nil, err
	}

	// The UPDATE matched no row: find out why so the caller gets the right
	// sentinel instead of a blanket "not found".
	existing, getErr := r.GetByID(ctx, id, userID)
	if getErr != nil {
		return nil, getErr
	}
	if existing.Status != domain.AnalysisStatusQueued && existing.Status != domain.AnalysisStatusWaitingForReference {
		return nil, domain.ErrAnalysisNotQueued
	}
	// Existed and was cancelable moments ago but the UPDATE still matched
	// zero rows: raced with a concurrent cancel. Report the same outcome either way.
	return nil, domain.ErrAnalysisNotQueued
}

// Retry moves a failed analysis back to queued, at the back of the FIFO
// order, without touching its stored recording or song reference (FR-26: no
// re-upload). It draws a fresh queue_seq from the same sequence Create uses,
// so it sorts after every job already waiting. It returns
// domain.ErrNotFound if the id doesn't exist or isn't owned by userID, and
// domain.ErrAnalysisNotFailed if it exists but isn't in the failed state.
func (r *AnalysisRepository) Retry(ctx context.Context, id, userID uuid.UUID) (*domain.Analysis, error) {
	const q = `
		UPDATE analyses
		SET status = 'queued',
			error_code = NULL,
			current_stage = NULL,
			current_stage_index = NULL,
			total_stages = NULL,
			current_stage_started_at = NULL,
			queue_stream_id = NULL,
			queue_position = NULL,
			queue_seq = nextval('analyses_queue_seq_seq')
		WHERE id = $1 AND user_id = $2 AND status = 'failed'
		RETURNING ` + analysisColumns

	updated, err := scanAnalysis(r.pool.QueryRow(ctx, q, id, userID))
	if err == nil {
		return updated, nil
	}
	if !errors.Is(err, domain.ErrNotFound) {
		return nil, err
	}

	// The UPDATE matched no row: find out why so the caller gets the right
	// sentinel instead of a blanket "not found".
	existing, getErr := r.GetByID(ctx, id, userID)
	if getErr != nil {
		return nil, getErr
	}
	if existing.Status != domain.AnalysisStatusFailed {
		return nil, domain.ErrAnalysisNotFailed
	}
	// Existed and was failed moments ago but the UPDATE still matched zero
	// rows: raced with a concurrent retry. Report the same outcome either way.
	return nil, domain.ErrAnalysisNotFailed
}

// RecalculatePositions reassigns a 1-based FIFO queue_position to every
// currently queued analysis, ordered by queue_seq, and returns only the
// rows whose position actually changed -- the set the caller needs to push
// over WebSocket (spec 10: "queue_position перераховується при кожному
// завершенні завдання"). Called after every enqueue and cancel.
func (r *AnalysisRepository) RecalculatePositions(ctx context.Context) (map[uuid.UUID]int, error) {
	const q = `
		WITH ranked AS (
			SELECT id, ROW_NUMBER() OVER (ORDER BY queue_seq) AS rn
			FROM analyses
			WHERE status = 'queued'
		)
		UPDATE analyses a
		SET queue_position = ranked.rn
		FROM ranked
		WHERE a.id = ranked.id AND a.queue_position IS DISTINCT FROM ranked.rn
		RETURNING a.id, ranked.rn`

	rows, err := r.pool.Query(ctx, q)
	if err != nil {
		return nil, fmt.Errorf("recalculate queue positions: %w", err)
	}
	defer rows.Close()

	changed := make(map[uuid.UUID]int)
	for rows.Next() {
		var id uuid.UUID
		var pos int
		if err := rows.Scan(&id, &pos); err != nil {
			return nil, fmt.Errorf("scan queue position: %w", err)
		}
		changed[id] = pos
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate queue positions: %w", err)
	}
	return changed, nil
}
