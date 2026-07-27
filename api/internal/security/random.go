package security

import (
	"crypto/rand"
	"encoding/base64"
	"fmt"
)

// RandomURLSafeToken returns a cryptographically random, base64url-encoded
// string built from nBytes of entropy. Shared by every place that needs an
// unguessable opaque token: refresh tokens, OAuth state, and PKCE verifiers.
func RandomURLSafeToken(nBytes int) (string, error) {
	buf := make([]byte, nBytes)
	if _, err := rand.Read(buf); err != nil {
		return "", fmt.Errorf("generate random token: %w", err)
	}
	return base64.RawURLEncoding.EncodeToString(buf), nil
}
