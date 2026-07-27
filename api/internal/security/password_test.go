package security_test

import (
	"testing"

	"github.com/stretchr/testify/require"

	"ai-vocal-coach/api/internal/security"
)

func TestPasswordHasher_HashAndVerify(t *testing.T) {
	h, err := security.NewPasswordHasher()
	require.NoError(t, err)

	hash, err := h.Hash("correct-horse-battery-staple")
	require.NoError(t, err)
	require.NotEqual(t, "correct-horse-battery-staple", hash, "the hash must never be the plaintext")

	ok, err := h.Verify("correct-horse-battery-staple", hash)
	require.NoError(t, err)
	require.True(t, ok)

	ok, err = h.Verify("wrong-password", hash)
	require.NoError(t, err)
	require.False(t, ok)
}

func TestPasswordHasher_SameInputDifferentSalt(t *testing.T) {
	h, err := security.NewPasswordHasher()
	require.NoError(t, err)

	h1, err := h.Hash("same-password")
	require.NoError(t, err)
	h2, err := h.Hash("same-password")
	require.NoError(t, err)
	require.NotEqual(t, h1, h2, "argon2id must salt every hash independently")
}

func TestPasswordHasher_DummyHash_StableAndUnmatched(t *testing.T) {
	h, err := security.NewPasswordHasher()
	require.NoError(t, err)

	require.Equal(t, h.DummyHash(), h.DummyHash(), "the dummy hash must be stable across calls")

	ok, err := h.Verify("some-guess", h.DummyHash())
	require.NoError(t, err)
	require.False(t, ok)
}
