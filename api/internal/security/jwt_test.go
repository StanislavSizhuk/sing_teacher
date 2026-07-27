package security_test

import (
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/stretchr/testify/require"

	"ai-vocal-coach/api/internal/domain"
	"ai-vocal-coach/api/internal/security"
)

func TestJWTIssuer_IssueAndParse_Roundtrip(t *testing.T) {
	issuer := security.NewJWTIssuer("a-secret-that-is-at-least-32-bytes-long", 15*time.Minute)
	userID := uuid.New()

	token, err := issuer.Issue(userID)
	require.NoError(t, err)
	require.NotEmpty(t, token)

	got, err := issuer.Parse(token)
	require.NoError(t, err)
	require.Equal(t, userID, got)
}

func TestJWTIssuer_ExpiredToken_Rejected(t *testing.T) {
	issuer := security.NewJWTIssuer("a-secret-that-is-at-least-32-bytes-long", -1*time.Second)
	token, err := issuer.Issue(uuid.New())
	require.NoError(t, err)

	_, err = issuer.Parse(token)
	require.ErrorIs(t, err, domain.ErrInvalidAccessToken)
}

func TestJWTIssuer_WrongSecret_Rejected(t *testing.T) {
	issuerA := security.NewJWTIssuer("a-secret-that-is-at-least-32-bytes-long", 15*time.Minute)
	issuerB := security.NewJWTIssuer("a-different-secret-at-least-32-bytes!!!", 15*time.Minute)

	token, err := issuerA.Issue(uuid.New())
	require.NoError(t, err)

	_, err = issuerB.Parse(token)
	require.ErrorIs(t, err, domain.ErrInvalidAccessToken)
}

func TestJWTIssuer_TamperedToken_Rejected(t *testing.T) {
	issuer := security.NewJWTIssuer("a-secret-that-is-at-least-32-bytes-long", 15*time.Minute)
	token, err := issuer.Issue(uuid.New())
	require.NoError(t, err)

	// Flip a character in the middle of the payload rather than the last
	// character of the signature: base64url's final character can carry
	// spare padding bits that some substitutions decode identically,
	// producing a false negative unrelated to signature verification.
	mid := len(token) / 2
	flipped := byte('a')
	if token[mid] == 'a' {
		flipped = 'b'
	}
	tampered := token[:mid] + string(flipped) + token[mid+1:]

	_, err = issuer.Parse(tampered)
	require.ErrorIs(t, err, domain.ErrInvalidAccessToken)
}

func TestJWTIssuer_GarbageToken_Rejected(t *testing.T) {
	issuer := security.NewJWTIssuer("a-secret-that-is-at-least-32-bytes-long", 15*time.Minute)
	_, err := issuer.Parse("not-a-jwt-at-all")
	require.ErrorIs(t, err, domain.ErrInvalidAccessToken)
}
