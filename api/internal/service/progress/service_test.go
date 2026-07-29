package progress_test

import (
	"context"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/stretchr/testify/require"

	"ai-vocal-coach/api/internal/domain"
	"ai-vocal-coach/api/internal/service/progress"
)

type fakeProgressRepository struct {
	byUser map[uuid.UUID][]domain.ProgressPoint
	err    error
}

func (f *fakeProgressRepository) ListByUser(_ context.Context, userID uuid.UUID) ([]domain.ProgressPoint, error) {
	if f.err != nil {
		return nil, f.err
	}
	return f.byUser[userID], nil
}

func TestService_ListByUser_ReturnsOnlyTheCallersPoints(t *testing.T) {
	userID := uuid.New()
	otherID := uuid.New()
	want := []domain.ProgressPoint{
		{AnalysisID: uuid.New(), OverallScore: 60, CreatedAt: time.Now().Add(-time.Hour)},
		{AnalysisID: uuid.New(), OverallScore: 75, CreatedAt: time.Now()},
	}
	repo := &fakeProgressRepository{byUser: map[uuid.UUID][]domain.ProgressPoint{
		userID:  want,
		otherID: {{AnalysisID: uuid.New(), OverallScore: 10, CreatedAt: time.Now()}},
	}}
	svc := progress.NewService(repo)

	got, err := svc.ListByUser(context.Background(), userID)
	require.NoError(t, err)
	require.Equal(t, want, got)
}

func TestService_ListByUser_PropagatesRepositoryError(t *testing.T) {
	boom := context.DeadlineExceeded
	repo := &fakeProgressRepository{err: boom}
	svc := progress.NewService(repo)

	_, err := svc.ListByUser(context.Background(), uuid.New())
	require.ErrorIs(t, err, boom)
}
