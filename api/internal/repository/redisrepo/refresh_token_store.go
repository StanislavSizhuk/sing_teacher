// Package redisrepo implements service-layer interfaces backed by Redis:
// refresh token rotation and the brute-force/resend throttles. Redis is the
// right store here because every one of these needs instant, TTL-based revocation.
package redisrepo

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"time"

	"github.com/google/uuid"
	"github.com/redis/go-redis/v9"

	"ai-vocal-coach/api/internal/domain"
	"ai-vocal-coach/api/internal/security"
)

// RefreshTokenStore issues and rotates opaque refresh tokens in Redis, grouped
// into "families" so that reuse of a rotated-away token revokes every token
// descended from the same login (spec 9.1).
type RefreshTokenStore struct {
	client *redis.Client
	ttl    time.Duration
	// grace is how long a just-rotated token is kept (marked "rotated" rather
	// than deleted) so a reuse replay landing moments later is still caught.
	grace time.Duration
}

// NewRefreshTokenStore builds a store whose tokens live for ttl.
func NewRefreshTokenStore(client *redis.Client, ttl time.Duration) *RefreshTokenStore {
	return &RefreshTokenStore{client: client, ttl: ttl, grace: 30 * time.Second}
}

type tokenStatus string

const (
	statusActive  tokenStatus = "active"
	statusRotated tokenStatus = "rotated"
)

type tokenRecord struct {
	UserID   uuid.UUID   `json:"user_id"`
	FamilyID uuid.UUID   `json:"family_id"`
	Status   tokenStatus `json:"status"`
}

func tokenKey(hash string) string          { return "auth:refresh:" + hash }
func familyTokensKey(id uuid.UUID) string  { return "auth:refresh-family:" + id.String() + ":tokens" }
func familyRevokedKey(id uuid.UUID) string { return "auth:refresh-family:" + id.String() + ":revoked" }
func userFamiliesKey(id uuid.UUID) string  { return "auth:refresh-user:" + id.String() + ":families" }

func newOpaqueToken() (raw string, hash string, err error) {
	raw, err = security.RandomURLSafeToken(32)
	if err != nil {
		return "", "", fmt.Errorf("generate refresh token: %w", err)
	}
	sum := sha256.Sum256([]byte(raw))
	return raw, hex.EncodeToString(sum[:]), nil
}

// Issue creates a brand new token family for userID and returns its first token.
func (s *RefreshTokenStore) Issue(ctx context.Context, userID uuid.UUID) (string, error) {
	return s.issueInFamily(ctx, userID, uuid.New())
}

func (s *RefreshTokenStore) issueInFamily(ctx context.Context, userID, familyID uuid.UUID) (string, error) {
	raw, hash, err := newOpaqueToken()
	if err != nil {
		return "", err
	}
	rec, err := json.Marshal(tokenRecord{UserID: userID, FamilyID: familyID, Status: statusActive})
	if err != nil {
		return "", fmt.Errorf("marshal token record: %w", err)
	}

	pipe := s.client.TxPipeline()
	pipe.Set(ctx, tokenKey(hash), rec, s.ttl)
	pipe.SAdd(ctx, familyTokensKey(familyID), hash)
	pipe.Expire(ctx, familyTokensKey(familyID), s.ttl)
	pipe.SAdd(ctx, userFamiliesKey(userID), familyID.String())
	pipe.Expire(ctx, userFamiliesKey(userID), s.ttl)
	if _, err := pipe.Exec(ctx); err != nil {
		return "", fmt.Errorf("persist refresh token: %w", err)
	}
	return raw, nil
}

// Rotate consumes token and issues its successor in the same family. Presenting
// a token that was already rotated away revokes the whole family and returns
// domain.ErrRefreshTokenReused.
func (s *RefreshTokenStore) Rotate(ctx context.Context, token string) (string, uuid.UUID, error) {
	sum := sha256.Sum256([]byte(token))
	hash := hex.EncodeToString(sum[:])

	raw, err := s.client.Get(ctx, tokenKey(hash)).Result()
	if errors.Is(err, redis.Nil) {
		return "", uuid.Nil, domain.ErrRefreshTokenInvalid
	}
	if err != nil {
		return "", uuid.Nil, fmt.Errorf("get refresh token: %w", err)
	}

	var rec tokenRecord
	if err := json.Unmarshal([]byte(raw), &rec); err != nil {
		return "", uuid.Nil, fmt.Errorf("unmarshal token record: %w", err)
	}

	if rec.Status == statusRotated {
		if err := s.revokeFamily(ctx, rec.FamilyID); err != nil {
			return "", uuid.Nil, err
		}
		return "", uuid.Nil, domain.ErrRefreshTokenReused
	}

	revoked, err := s.client.Exists(ctx, familyRevokedKey(rec.FamilyID)).Result()
	if err != nil {
		return "", uuid.Nil, fmt.Errorf("check family revocation: %w", err)
	}
	if revoked > 0 {
		return "", uuid.Nil, domain.ErrRefreshTokenInvalid
	}

	rotatedRec, err := json.Marshal(tokenRecord{UserID: rec.UserID, FamilyID: rec.FamilyID, Status: statusRotated})
	if err != nil {
		return "", uuid.Nil, fmt.Errorf("marshal rotated record: %w", err)
	}
	if err := s.client.Set(ctx, tokenKey(hash), rotatedRec, s.grace).Err(); err != nil {
		return "", uuid.Nil, fmt.Errorf("mark token rotated: %w", err)
	}

	newToken, err := s.issueInFamily(ctx, rec.UserID, rec.FamilyID)
	if err != nil {
		return "", uuid.Nil, err
	}
	return newToken, rec.UserID, nil
}

// Revoke invalidates a single token (logout of the current session only).
func (s *RefreshTokenStore) Revoke(ctx context.Context, token string) error {
	sum := sha256.Sum256([]byte(token))
	if err := s.client.Del(ctx, tokenKey(hex.EncodeToString(sum[:]))).Err(); err != nil {
		return fmt.Errorf("revoke refresh token: %w", err)
	}
	return nil
}

// RevokeAllForUser invalidates every token family for the user (logout of all
// devices, FR-06; also called when the account itself is deleted).
func (s *RefreshTokenStore) RevokeAllForUser(ctx context.Context, userID uuid.UUID) error {
	families, err := s.client.SMembers(ctx, userFamiliesKey(userID)).Result()
	if err != nil {
		return fmt.Errorf("list token families: %w", err)
	}
	for _, raw := range families {
		familyID, err := uuid.Parse(raw)
		if err != nil {
			continue // defensive: never let a corrupt family id block the rest of the sweep
		}
		if err := s.revokeFamily(ctx, familyID); err != nil {
			return err
		}
	}
	return s.client.Del(ctx, userFamiliesKey(userID)).Err()
}

func (s *RefreshTokenStore) revokeFamily(ctx context.Context, familyID uuid.UUID) error {
	hashes, err := s.client.SMembers(ctx, familyTokensKey(familyID)).Result()
	if err != nil {
		return fmt.Errorf("list family tokens: %w", err)
	}

	pipe := s.client.TxPipeline()
	for _, h := range hashes {
		pipe.Del(ctx, tokenKey(h))
	}
	// Outlives any token that could still reference this family, which was
	// issued at most s.ttl ago.
	pipe.Set(ctx, familyRevokedKey(familyID), "1", s.ttl)
	pipe.Del(ctx, familyTokensKey(familyID))
	_, err = pipe.Exec(ctx)
	if err != nil {
		return fmt.Errorf("revoke token family: %w", err)
	}
	return nil
}
