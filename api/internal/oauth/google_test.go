package oauth

import (
	"testing"

	"github.com/stretchr/testify/require"
)

// Known-answer vector from RFC 7636 Appendix B, so a regression to the wrong
// hash or encoding (not just a change mirrored from the implementation
// itself) fails this test.
func TestCodeChallengeS256_RFC7636KnownVector(t *testing.T) {
	got := codeChallengeS256("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk")
	require.Equal(t, "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM", got)
}

func TestCodeChallengeS256_Deterministic(t *testing.T) {
	require.Equal(t, codeChallengeS256("same-verifier"), codeChallengeS256("same-verifier"))
}

func TestCodeChallengeS256_DifferentVerifiersProduceDifferentChallenges(t *testing.T) {
	require.NotEqual(t, codeChallengeS256("verifier-one"), codeChallengeS256("verifier-two"))
}

// The challenge travels as a URL query parameter (spec 9.1), so it must never
// contain the standard-base64 characters ('+', '/') or padding ('=').
func TestCodeChallengeS256_IsURLSafeAndUnpadded(t *testing.T) {
	got := codeChallengeS256("some-code-verifier-value")
	require.NotContains(t, got, "+")
	require.NotContains(t, got, "/")
	require.NotContains(t, got, "=")
}
