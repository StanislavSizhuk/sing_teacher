package redisrepo

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"time"

	"github.com/redis/go-redis/v9"
)

// Brute-force policy constants (spec 9.1: exponential delay per (email, IP),
// lockout for 15 minutes after 10 failures).
const (
	loginMaxFailures  = 10
	loginLockDuration = 15 * time.Minute
	loginMaxBackoff   = 60 * time.Second
)

// LoginThrottle rate-limits login attempts per (email, IP) key.
type LoginThrottle struct {
	client *redis.Client
}

// NewLoginThrottle builds a LoginThrottle backed by client.
func NewLoginThrottle(client *redis.Client) *LoginThrottle {
	return &LoginThrottle{client: client}
}

type loginState struct {
	Failures      int       `json:"failures"`
	LockedUntil   time.Time `json:"locked_until"`
	NextAllowedAt time.Time `json:"next_allowed_at"`
}

func loginKey(key string) string { return "auth:loginfail:" + key }

func (t *LoginThrottle) load(ctx context.Context, key string) (loginState, error) {
	raw, err := t.client.Get(ctx, loginKey(key)).Result()
	if errors.Is(err, redis.Nil) {
		return loginState{}, nil
	}
	if err != nil {
		return loginState{}, fmt.Errorf("load login throttle state: %w", err)
	}
	var s loginState
	if err := json.Unmarshal([]byte(raw), &s); err != nil {
		return loginState{}, fmt.Errorf("unmarshal login throttle state: %w", err)
	}
	return s, nil
}

// Check reports whether key is currently locked out or in a backoff window,
// and if so for how much longer.
func (t *LoginThrottle) Check(ctx context.Context, key string) (locked bool, retryAfter time.Duration, err error) {
	s, err := t.load(ctx, key)
	if err != nil {
		return false, 0, err
	}
	now := time.Now()
	if now.Before(s.LockedUntil) {
		return true, s.LockedUntil.Sub(now), nil
	}
	if now.Before(s.NextAllowedAt) {
		return true, s.NextAllowedAt.Sub(now), nil
	}
	return false, 0, nil
}

// RecordFailure counts one more failed attempt against key and extends the
// backoff (or triggers the hard lockout past loginMaxFailures).
//
// A 429 with Retry-After is used instead of sleeping inside the handler: it
// slows an attacker down just the same without holding a goroutine and
// connection open for the delay.
func (t *LoginThrottle) RecordFailure(ctx context.Context, key string) error {
	s, err := t.load(ctx, key)
	if err != nil {
		return err
	}
	now := time.Now()
	s.Failures++
	if s.Failures >= loginMaxFailures {
		s.LockedUntil = now.Add(loginLockDuration)
	} else {
		backoff := time.Duration(1<<uint(s.Failures)) * time.Second
		if backoff > loginMaxBackoff {
			backoff = loginMaxBackoff
		}
		s.NextAllowedAt = now.Add(backoff)
	}
	raw, err := json.Marshal(s)
	if err != nil {
		return fmt.Errorf("marshal login throttle state: %w", err)
	}
	if err := t.client.Set(ctx, loginKey(key), raw, loginLockDuration).Err(); err != nil {
		return fmt.Errorf("persist login throttle state: %w", err)
	}
	return nil
}

// Reset clears all throttle state for key, called after a successful login.
func (t *LoginThrottle) Reset(ctx context.Context, key string) error {
	if err := t.client.Del(ctx, loginKey(key)).Err(); err != nil {
		return fmt.Errorf("reset login throttle state: %w", err)
	}
	return nil
}
