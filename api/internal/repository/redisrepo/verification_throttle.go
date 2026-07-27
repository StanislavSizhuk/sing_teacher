package redisrepo

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/google/uuid"
	"github.com/redis/go-redis/v9"
)

// Resend policy constants (FR-04: at most once per 60s, max 5 per day).
const (
	verificationCooldown = 60 * time.Second
	verificationDailyMax = 5
	verificationDailyTTL = 24 * time.Hour
)

// VerificationThrottle rate-limits verification-code resends per user.
type VerificationThrottle struct {
	client *redis.Client
}

// NewVerificationThrottle builds a VerificationThrottle backed by client.
func NewVerificationThrottle(client *redis.Client) *VerificationThrottle {
	return &VerificationThrottle{client: client}
}

func verificationCooldownKey(userID uuid.UUID) string {
	return "auth:verify-cooldown:" + userID.String()
}
func verificationDailyKey(userID uuid.UUID) string { return "auth:verify-daily:" + userID.String() }

// AllowResend reports whether a new code may be sent now. When it returns
// false, retryAfter is how long until the next attempt may succeed, and
// dailyLimitReached distinguishes the daily cap from the 60s cooldown.
func (t *VerificationThrottle) AllowResend(ctx context.Context, userID uuid.UUID) (allowed bool, retryAfter time.Duration, dailyLimitReached bool, err error) {
	cooldownKey := verificationCooldownKey(userID)
	exists, err := t.client.Exists(ctx, cooldownKey).Result()
	if err != nil {
		return false, 0, false, fmt.Errorf("check resend cooldown: %w", err)
	}
	if exists > 0 {
		ttl, err := t.client.TTL(ctx, cooldownKey).Result()
		if err != nil {
			return false, 0, false, fmt.Errorf("read resend cooldown ttl: %w", err)
		}
		return false, ttl, false, nil
	}

	dailyKey := verificationDailyKey(userID)
	count, err := t.client.Get(ctx, dailyKey).Int()
	if err != nil && !errors.Is(err, redis.Nil) {
		return false, 0, false, fmt.Errorf("read resend daily count: %w", err)
	}
	if count >= verificationDailyMax {
		ttl, err := t.client.TTL(ctx, dailyKey).Result()
		if err != nil {
			return false, 0, false, fmt.Errorf("read resend daily ttl: %w", err)
		}
		return false, ttl, true, nil
	}

	if err := t.client.Set(ctx, cooldownKey, "1", verificationCooldown).Err(); err != nil {
		return false, 0, false, fmt.Errorf("set resend cooldown: %w", err)
	}
	newCount, err := t.client.Incr(ctx, dailyKey).Result()
	if err != nil {
		return false, 0, false, fmt.Errorf("increment resend daily count: %w", err)
	}
	if newCount == 1 {
		if err := t.client.Expire(ctx, dailyKey, verificationDailyTTL).Err(); err != nil {
			return false, 0, false, fmt.Errorf("set resend daily ttl: %w", err)
		}
	}
	return true, 0, false, nil
}
