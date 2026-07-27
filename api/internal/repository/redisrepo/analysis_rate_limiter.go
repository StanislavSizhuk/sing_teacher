package redisrepo

import (
	"context"
	"fmt"
	"strconv"
	"time"

	"github.com/google/uuid"
	"github.com/redis/go-redis/v9"
)

// AnalysisRateLimiter enforces USER_ANALYSES_PER_HOUR (spec 10, FR-24) with
// a sliding window: each allowed request adds a millisecond-timestamped
// member to a per-user sorted set, and members older than the window are
// trimmed before every check.
type AnalysisRateLimiter struct {
	client *redis.Client
	limit  int
	window time.Duration
}

// NewAnalysisRateLimiter builds a limiter allowing at most limit requests
// per window, per user.
func NewAnalysisRateLimiter(client *redis.Client, limit int, window time.Duration) *AnalysisRateLimiter {
	return &AnalysisRateLimiter{client: client, limit: limit, window: window}
}

func analysisRateLimitKey(userID uuid.UUID) string {
	return "analyses:rate:" + userID.String()
}

// Allow reports whether userID may start another analysis now, and if so,
// records this attempt. When it returns false, retryAfter is how long until
// the oldest entry in the window ages out and a slot frees up.
//
// Scores are Unix milliseconds, not nanoseconds: Redis sorted-set scores are
// float64, and a nanosecond timestamp exceeds float64's 53-bit exact integer
// range, which would corrupt ordering.
func (l *AnalysisRateLimiter) Allow(ctx context.Context, userID uuid.UUID) (allowed bool, retryAfter time.Duration, err error) {
	key := analysisRateLimitKey(userID)
	now := time.Now()
	cutoffMillis := now.Add(-l.window).UnixMilli()

	if err := l.client.ZRemRangeByScore(ctx, key, "0", strconv.FormatInt(cutoffMillis, 10)).Err(); err != nil {
		return false, 0, fmt.Errorf("trim analysis rate window: %w", err)
	}

	count, err := l.client.ZCard(ctx, key).Result()
	if err != nil {
		return false, 0, fmt.Errorf("count analysis rate window: %w", err)
	}
	if count >= int64(l.limit) {
		oldest, err := l.client.ZRangeWithScores(ctx, key, 0, 0).Result()
		if err != nil {
			return false, 0, fmt.Errorf("read oldest analysis rate entry: %w", err)
		}
		if len(oldest) > 0 {
			expiresAt := time.UnixMilli(int64(oldest[0].Score)).Add(l.window)
			if until := time.Until(expiresAt); until > 0 {
				retryAfter = until
			}
		}
		return false, retryAfter, nil
	}

	nowMillis := now.UnixMilli()
	member := fmt.Sprintf("%d-%s", nowMillis, uuid.NewString())
	pipe := l.client.TxPipeline()
	pipe.ZAdd(ctx, key, redis.Z{Score: float64(nowMillis), Member: member})
	pipe.Expire(ctx, key, l.window)
	if _, err := pipe.Exec(ctx); err != nil {
		return false, 0, fmt.Errorf("record analysis rate entry: %w", err)
	}
	return true, 0, nil
}
